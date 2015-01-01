import sys
import json
from flask import render_template, abort, request, Response, jsonify, url_for, redirect, flash
from flask_mail import Message
from requests import get, post
from webargs.flaskparser import FlaskParser
from flask.ext.paginate import Pagination
from arguments import search_args
from utils import (pull_feed, create_display_links, getbrowsecontent,
                   SearchPublications, change_to_pubs_test, generate_auth_header, munge_pubdata_for_display)
from forms import ContactForm, SearchForm, NumSeries, LoginForm
from canned_text import EMAIL_RESPONSE
from pubs_ui import app, mail
from datetime import date, timedelta
from dateutil import parser as dateparser
from flask_login import (LoginManager, login_required, login_user, logout_user, UserMixin)
from itsdangerous import URLSafeTimedSerializer
from operator import itemgetter
import arrow

# set UTF-8 to be default throughout app
reload(sys)
sys.setdefaultencoding("utf-8")

pub_url = app.config['PUB_URL']
lookup_url = app.config['LOOKUP_URL']
supersedes_url = app.config['SUPERSEDES_URL']
browse_url = app.config['BROWSE_URL']
search_url = app.config['BASE_SEARCH_URL']
citation_url = app.config['BASE_CITATION_URL']
browse_replace = app.config['BROWSE_REPLACE']
contact_recipients = app.config['CONTACT_RECIPIENTS']
replace_pubs_with_pubs_test = app.config.get('REPLACE_PUBS_WITH_PUBS_TEST')
robots_welcome = app.config.get('ROBOTS_WELCOME')
json_ld_id_base_url = app.config.get('JSON_LD_ID_BASE_URL')
google_webmaster_tools_code = app.config.get('GOOGLE_WEBMASTER_TOOLS_CODE')
auth_endpoint_url = app.config.get('AUTH_ENDPOINT_URL')
preview_endpoint_url = app.config.get('PREVIEW_ENDPOINT_URL')
max_age = app.config["REMEMBER_COOKIE_DURATION"].total_seconds()
login_page_path = app.config['LOGIN_PAGE_PATH']


# should requests verify the certificates for ssl connections
verify_cert = app.config['VERIFY_CERT']

# Login_serializer used to encrypt and decrypt the cookie token for the remember
# me option of flask-login
login_serializer = URLSafeTimedSerializer(app.secret_key)

# Flask-Login Login Manager
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = login_page_path


class User(UserMixin):
    """
    User Class for flask-Login
    """
    def __init__(self, user_ad_username=None, pubs_auth_token=None):
        self.id = user_ad_username
        self.auth_token = pubs_auth_token

    def is_authenticated(self):
        return True

    def is_active(self):
        return True

    def is_anonymous(self):
        return False

    def get_auth_token(self):
        """
        Encode a secure token for cookie
        """
        data = [str(self.id), self.auth_token]
        return login_serializer.dumps(data)

    @staticmethod
    def get(userid, token):
        """
        Static method to search the database and see if userid exists.  If it
        does exist then return a User Object.  If not then return None as
        required by Flask-Login.
        """
        # since we are offloading authentication of the user to the backend, we are assuming that if we have an
        # unexpired token, we have a valid user, so we are just putting in a dummy string to keep flask-login happy
        print "get! ", str(userid)
        if userid:
            return User(userid, token)
        return None


@login_manager.user_loader
def load_user(userid):
    """
    Flask-Login user_loader callback.
    The user_loader function asks this function to get a User Object or return
    None based on the userid.
    The userid was stored in the session environment by Flask-Login.
    user_loader stores the returned User object in current_user during every
    flask request.
    """

    token_cookie = request.cookies.get('remember_token')
    session_data = login_serializer.loads(token_cookie, max_age=max_age)
    # get the token from the session data
    mypubs_token = session_data[1]
    return User.get(userid, mypubs_token)


@login_manager.token_loader
def load_token(token):
    """
    Flask-Login token_loader callback.
    The token_loader function asks this function to take the token that was
    stored on the users computer process it to check if its valid and then
    return a User Object if its valid or None if its not valid.
    """

    # The Token itself was generated by User.get_auth_token.  So it is up to
    # us to known the format of the token data itself.

    # The Token was encrypted using itsdangerous.URLSafeTimedSerializer which
    # allows us to have a max_age on the token itself.  When the cookie is stored
    # on the users computer it also has a exipry date, but could be changed by
    # the user, so this feature allows us to enforce the exipry date of the token
    # server side and not rely on the users cookie to exipre.
    token_max_age = max_age

    # Decrypt the Security Token, data = [ad_user_username, user_ad_token]
    data = login_serializer.loads(token, max_age=token_max_age)

    # generate the user object based on the contents of the cookie, if the cookie isn't expired
    if data:
        user = User(data[0], data[1])
    else:
        user = None
    # return the user
    if user:
        return user
    return None


@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404


@app.route("/logout/")
def logout_page():
    """
    Web Page to Logout User, then Redirect them to Index Page.
    """
    auth_header = generate_auth_header(request)
    logout_url = auth_endpoint_url+'logout'
    response = post(logout_url, headers=auth_header, verify=verify_cert)
    if response.status_code == 200:
        print 'logout works!'

    logout_user()

    return redirect(url_for('index'))


@app.route("/login/", methods=["GET", "POST"])
def login_page():
    """
    Web Page to Display Login Form and process form.
    """
    form = LoginForm()
    error = None
    if request.method == "POST":
        # take the form data and put it into the payload to send to the pubs auth endpoint
        payload = {'username': request.form['username'], 'password': request.form['password']}
        # POST the payload to the pubs auth endpoint
        pubs_login_url = auth_endpoint_url+'token'
        print pubs_login_url
        mp_response = post(pubs_login_url, data=payload, verify=verify_cert)
        # if the pubs endpoint login is successful, then proceed with logging in
        if mp_response.status_code == 200:
            print "pubs login worked"
            user = User(request.form['username'], mp_response.json().get('token'))
            print "user was created"
            login_user(user, remember=True)
            flash('You were successfully logged in')
            return redirect(request.args.get("next") or "/")
        else:
            error = 'Username or Password is invalid '+str(mp_response.status_code)

    return render_template("login.html", form=form, error=error)


@app.route("/preview/<index_id>")
@login_required
def restricted_page(index_id):
    """
    web page which is restricted and requires the user to be logged in.
    """

    # generate the auth header from the request
    auth_header = generate_auth_header(request)
    # build the url to call the endpoint
    published_status = get(pub_url + 'publication/' + index_id,
                           params={'mimetype': 'json'}, verify=verify_cert).status_code
    # go out to mypubs and get the record
    response = get(preview_endpoint_url+index_id+'/preview', headers=auth_header, verify=verify_cert,
                   params={'mimetype': 'json'})
    print "preview status code: ", response.status_code
    if response.status_code == 200:
        record = response.json()
        pubdata = munge_pubdata_for_display(record, replace_pubs_with_pubs_test, supersedes_url, json_ld_id_base_url)
        return render_template("preview.html", indexID=index_id, pubdata=pubdata)
    # if the publication has been published (so it is out of mypubs) redirect to the right URL
    elif response.status_code == 404 and published_status == 200:
        return redirect(url_for('publication', indexId=index_id))
    elif response.status_code == 404 and published_status == 404:
        return render_template('404.html')


@app.route('/robots.txt')
def robots():
    return render_template('robots.txt', robots_welcome=robots_welcome)

@app.route('/opensearch.xml')
def open_search():
    return render_template('opensearch.xml')


@app.route('/' + google_webmaster_tools_code + '.html')
def webmaster_tools_verification():
    return render_template('google_site_verification.html')


@app.route('/')
def index():
    sp = SearchPublications(search_url)
    recent_publications_resp = sp.get_pubs_search_results(params={'pub_x_days': 7,
                                                                  'page_size': 6})  # bring back recent publications
    recent_pubs_content = recent_publications_resp[0]
    try:
        pubs_records = recent_pubs_content['records']
        for record in pubs_records:
            record = create_display_links(record)
            if replace_pubs_with_pubs_test:
                record['displayLinks']['Thumbnail'][0]['url'] = change_to_pubs_test(
                    record['displayLinks']['Thumbnail'][0]['url'])

    except TypeError:
        pubs_records = []  # return an empty list recent_pubs_content is None (e.g. the service is down)
    form = SearchForm(None, obj=request.args)
    form.advanced.data = True
    return render_template('home.html',
                           recent_publications=pubs_records,
                           form=form,
                           advanced=request.args.get('advanced'))


# contact form
@app.route('/contact', methods=['GET', 'POST'])
def contact():
    contact_form = ContactForm()
    if request.method == 'POST':
        if contact_form.validate_on_submit():
            human_name = contact_form.name.data
            human_email = contact_form.email.data
            if human_name:
                sender_str = '({name}, {email})'.format(name=human_name, email=human_email)
            else:
                sender_str = '({email})'.format(email=human_email)
            subject_line = 'Pubs Warehouse User Comments'  # this is want Remedy filters on to determine if an email
            # goes to the pubs support group
            message_body = contact_form.message.data
            message_content = EMAIL_RESPONSE.format(contact_str=sender_str, message_body=message_body)
            msg = Message(subject=subject_line,
                          sender=(human_name, human_email),
                          reply_to=('PUBSV2_NO_REPLY', 'pubsv2_no_reply@usgs.gov'),
                          # this is not what Remedy filters on to determine if a message
                          # goes to the pubs support group...
                          recipients=contact_recipients,
                          # will go to servicedesk@usgs.gov if application has DEBUG = False
                          body=message_content)
            mail.send(msg)
            return redirect(url_for(
                'contact_confirmation'))  # redirect to a conf page after successful validation and message sending
        else:
            return render_template('contact.html',
                                   contact_form=contact_form)  # redisplay the form with errors if validation fails
    elif request.method == 'GET':
        return render_template('contact.html', contact_form=contact_form)


@app.route('/contact_confirm')
def contact_confirmation():
    confirmation_message = 'Thank you for contacting the USGS Publications Warehouse support team.'
    return render_template('contact_confirm.html', confirm_message=confirmation_message)


# leads to rendered html for publication page
@app.route('/publication/<index_id>')
def publication(index_id):
    r = get(pub_url + 'publication/' + index_id, params={'mimetype': 'json'}, verify=verify_cert)
    if r.status_code == 404:
        return render_template('404.html')
    pubreturn = r.json()
    pubdata = munge_pubdata_for_display(pubreturn, replace_pubs_with_pubs_test, supersedes_url, json_ld_id_base_url)
    if 'mimetype' in request.args and request.args.get("mimetype") == 'json':
        return jsonify(pubdata)
    else:
        return render_template('publication.html', indexID=index_id, pubdata=pubdata)


# leads to json for selected endpoints
@app.route('/lookup/<endpoint>')
def lookup(endpoint):
    endpoint_list = ['costcenters', 'publicationtypes', 'publicationsubtypes', 'publicationseries']
    endpoint = endpoint.lower()
    if endpoint in endpoint_list:
        r = get(lookup_url + endpoint, params={'mimetype': 'json'}, verify=verify_cert).json()
        return Response(json.dumps(r), mimetype='application/json')
    else:
        abort(404)


@app.route('/documentation/faq')
def faq():
    app.logger.info('The FAQ function is being called')
    feed_url = 'https://internal.cida.usgs.gov/wiki/createrssfeed.action?types=page&spaces=PUBSWI&title=Pubs+Other+Resources&labelString=pw_faq&excludedSpaceKeys%3D&sort=modified&maxResults=10&timeSpan=3600&showContent=true&confirm=Create+RSS+Feed'
    return render_template('faq.html', faq_content=pull_feed(feed_url))


@app.route('/documentation/usgs_series')
def usgs_series():
    app.logger.info('The USGS Series function is being called')
    feed_url = 'https://internal.cida.usgs.gov/wiki/createrssfeed.action?types=page&spaces=PUBSWI&title=USGS+Series+Definitions&labelString=usgs_series&excludedSpaceKeys%3D&sort=modified&maxResults=10&timeSpan=3600&showContent=true&confirm=Create+RSS+Feed'
    return render_template('usgs_series.html', usgs_series_content=pull_feed(feed_url))


@app.route('/documentation/web_service_documentation')
def web_service_docs():
    app.logger.info('The web_service_docs function is being called')
    feed_url = 'https://internal.cida.usgs.gov/wiki/createrssfeed.action?types=page&spaces=PUBSWI&title=Pubs+Other+Resources&labelString=pubs_webservice_docs&excludedSpaceKeys%3D&sort=modified&maxResults=10&timeSpan=3600&showContent=true&confirm=Create+RSS+Feed'
    return render_template('webservice_docs.html', web_service_docs=pull_feed(feed_url))


@app.route('/documentation/other_resources')
def other_resources():
    app.logger.info('The other_resources function is being called')
    feed_url = 'https://internal.cida.usgs.gov/wiki/createrssfeed.action?types=page&spaces=PUBSWI&title=Pubs+Other+Resources&labelString=other_resources&excludedSpaceKeys%3D&sort=modified&maxResults=10&timeSpan=3600&showContent=true&confirm=Create+RSS+Feed'
    return render_template('other_resources.html', other_resources=pull_feed(feed_url))


@app.route('/browse/', defaults={'path': ''})
@app.route('/browse/<path:path>')
def browse(path):
    app.logger.info("path: " + path)
    browsecontent = getbrowsecontent(browse_url + path, browse_replace)
    return render_template('browse.html', browsecontent=browsecontent)


# this takes advantage of the webargs package, which allows for multiple parameter entries. e.g. year=1981&year=1976
@app.route('/search', methods=['GET'])
def search_results():
    parser = FlaskParser()
    search_kwargs = parser.parse(search_args, request)
    form = SearchForm(None, obj=request.args, )
    # populate form based on parameter
    form.advanced.data = True
    form_element_list = ['q', 'title', 'contributingOffice', 'contributor', 'typeName', 'subtypeName', 'seriesName',
                         'reportNumber', 'year']
    for element in form_element_list:
        if len(search_kwargs[element]) > 0:
            form[element].data = search_kwargs[element][0]
    if search_kwargs.get('page_size') is None:
        search_kwargs['page_size'] = '25'
    if search_kwargs.get('page') is None:
        search_kwargs['page'] = '1'
    if search_kwargs.get('page_number') is None and search_kwargs.get('page') is not None:
        search_kwargs['page_number'] = search_kwargs['page']

    sp = SearchPublications(search_url)
    search_results_response, resp_status_code = sp.get_pubs_search_results(
        params=search_kwargs)  # go out to the pubs API and get the search results
    try:
        search_result_records = search_results_response['records']
        record_count = search_results_response['recordCount']
        pagination = Pagination(page=int(search_kwargs['page_number']), total=record_count,
                                per_page=int(search_kwargs['page_size']), record_name='Search Results', bs_version=3)
        search_service_down = None
        start_plus_size = int(search_results_response['pageRowStart']) + int(search_results_response['pageSize'])
        if record_count < start_plus_size:
            record_max = record_count
        else:
            record_max = start_plus_size

        result_summary = {'record_count': record_count, 'page_number': search_results_response['pageNumber'],
                          'records_per_page': search_results_response['pageSize'],
                          'record_min': (int(search_results_response['pageRowStart']) + 1), 'record_max': record_max}
    except TypeError:
        search_result_records = None
        pagination = None
        search_service_down = 'The backend services appear to be down with a {0} status.'.format(resp_status_code)
        result_summary = {}
    return render_template('search_results.html',
                           advanced=search_kwargs['advanced'],
                           result_summary=result_summary,
                           search_result_records=search_result_records,
                           pagination=pagination,
                           search_service_down=search_service_down,
                           form=form)


@app.route('/site-map')
def site_map():
    """
    View for troubleshooting application URL rules
    """
    app_urls = []

    for url_rule in app.url_map.iter_rules():
        app_urls.append((str(url_rule), str(url_rule.endpoint)))

    return render_template('site_map.html', app_urls=app_urls)


@app.route('/newpubs', methods=['GET'])
def new_pubs():
    num_form = NumSeries()
    sp = SearchPublications(search_url)
    search_kwargs = {'pub_x_days': 30, "page_size": 500}  # bring back recent publications

    # Search if num_series subtype was checked in form
    if request.args.get('num_series') == 'y':
        num_form.num_series.data = True
        search_kwargs['subtypeName'] = 'USGS Numbered Series'

    # Handles dates from form. Searches back to date selected or defaults to past 30 days.
    if request.args.get('date_range'):
        time_diff = date.today() - dateparser.parse(request.args.get('date_range')).date()
        day_diff = time_diff.days
        if not day_diff > 0:
            num_form.date_range.data = date.today() - timedelta(30)
            search_kwargs['pub_x_days'] = 30
        else:
            num_form.date_range.data = dateparser.parse(request.args.get('date_range'))
            search_kwargs['pub_x_days'] = day_diff
    else:
        num_form.date_range.data = date.today() - timedelta(30)

    recent_publications_resp = sp.get_pubs_search_results(params=search_kwargs)
    recent_pubs_content = recent_publications_resp[0]

    try:
        pubs_records = recent_pubs_content['records']
        pubs_records.sort(key=itemgetter('displayToPublicDate'), reverse=True)
        for record in pubs_records:
            record['FormattedDisplayToPublicDate'] = \
                arrow.get(record['displayToPublicDate']).format('MMMM DD, YYYY HH:mm')
    except TypeError:
        pubs_records = []  # return an empty list recent_pubs_content is None (e.g. the service is down)

    return render_template('new_pubs.html',
                           new_pubs=pubs_records,
                           num_form=num_form)


@app.route('/legacysearch/search:advance/page=1/series_cd=<series_code>/year=<pub_year>/report_number=<report_number>')
@app.route('/legacysearch/search:advance/page=1/series_cd=<series_code>/report_number=<report_number>')
def legacy_search(series_code=None, report_number=None, pub_year=None):
    """
    This is a function to deal with the fact that the USGS store has dumb links to the warehouse
    based on the legacy search, which had all the query params in a backslash-delimited group.  A couple lines of
    javascript on the index page (see the bottom script block on the index page) passes the legacy query string to this
    function, and then this function reinterprets the string and redirects to the new search.

    :param series_code: the series code, which we will have to map to series name
    :param pub_year: the publication year, two digit, so we will have to make a guess as to what century they want
    :param report_number: report number- we can generally just pass this through
    :return: redirect to new search page with legacy arguments mapped to new arguments
    """
    # all the pubcodes that might be coming from the USGS store
    usgs_series_codes = {'AR': 'Annual Report', 'A': 'Antarctic Map', 'B': 'Bulletin', 'CIR': 'Circular',
                         'CP': 'Circum-Pacific Map', 'COAL': 'Coal Map', 'DS': 'Data Series', 'FS': 'Fact Sheet',
                         'GF': 'Folios of the Geologic Atlas', 'GIP': 'General Information Product',
                         'GQ': 'Geologic Quadrangle', 'GP': 'Geophysical Investigation Map', 'HA': 'Hydrologic Atlas',
                         'HU': 'Hydrologic Unit', 'I': 'IMAP', 'L': 'Land Use/ Land Cover',
                         'MINERAL': 'Mineral Commodities Summaries', 'MR': 'Mineral Investigations Resource Map',
                         'MF': 'Miscellaneous Field Studies Map', 'MB': 'Missouri Basin Study', 'M': 'Monograph',
                         'OC': 'Oil and Gas Investigation Chart', 'OM': 'Oil and Gas Investigation Map',
                         'OFR': 'Open-File Report', 'PP': 'Professional Paper', 'RP': 'Resource Publication',
                         'SIM': 'Scientific Investigations Map', 'SIR': 'Scientific Investigations Report',
                         'TM': 'Techniques and Methods', 'TWRI': 'Techniques of Water-Resource Investigation',
                         'TEI': 'Trace Elements Investigations', 'TEM': 'Trace Elements Memorandum',
                         'WDR': 'Water Data Report', 'WSP': 'Water Supply Paper',
                         'WRI': 'Water-Resources Investigations Report'}

    # horrible hack to deal with the fact that the USGS store apparently never heard of 4 digit dates

    if pub_year is not None:
        if 30 <= int(pub_year) < 100:
            pub_year = ''.join(['19', pub_year])
        elif int(pub_year) < 30:
            pub_year = ''.join(['20', pub_year])

    return redirect(url_for('search_results', seriesName=usgs_series_codes.get(series_code), reportNumber=report_number,
                    year=pub_year, advanced=True))


@app.route('/unapi')
def unapi():
    """
    this is an unapi format, which appears to be the only way to get a good export to zotero that has all the Zotero fields
    Documented here: http://unapi.info/specs/
    :return:
    """
    formats = {'rdf_bibliontology': {'type': 'application/xml', 'docs': "http://bibliontology.com/specification"}}
    unapi_id = request.args.get('id')
    print "unapi id! ", unapi_id
    unapi_format = request.args.get('format')
    if unapi_format is None or unapi_format not in formats:
        return render_template('unapi_formats.xml', unapi_id=unapi_id, formats=formats,  mimetype='text/xml')
    if unapi_id is not None and unapi_format in formats:
        r = get(pub_url + 'publication/' + unapi_id, params={'mimetype': 'json'}, verify=verify_cert)
        if r.status_code == 404:
            return render_template('404.html'), 404
        pubdata = r.json()
        print pubdata
        return render_template('rdf_bibliontology.rdf', pubdata=pubdata, formats=formats,  mimetype='text/xml')

