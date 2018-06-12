// Karma configuration

var sourcePreprocessors = ['coverage'];
function isDebug(argument) {
    return argument === '--debug';
};
if (process.argv.some(isDebug)) {
    sourcePreprocessors = [];
}

module.exports = function(config) {
  config.set({

    // base path that will be used to resolve all patterns (eg. files, exclude)
    basePath: '../../..',


    // frameworks to use
    // available frameworks: https://npmjs.org/browse/keyword/karma-adapter
    frameworks: ['jasmine', 'sinon'],


    // list of files / patterns to load in the browser
    files: [
    	'bower_components/moment/min/moment.min.js',
		'bower_components/underscore/underscore-min.js',
		'bower_components/jquery/dist/jquery.js',
		'tests/js/metrics/resources/config.js',
		'scripts/metrics/*.js',
		'tests/js/metrics/*.js'
		],


    // list of files to exclude
    exclude: [
        'scripts/metrics/pubGraphController.js',
		'scripts/metrics/pubsAcquisitionGraphController.js',
		'scripts/metrics/pubsGraphController.js'
    ],


    // preprocess matching files before serving them to the browser
    // available preprocessors: https://npmjs.org/browse/keyword/karma-preprocessor
    preprocessors: {
      // source files, that you wanna generate coverage for
      // do not include tests or libraries
      // (these files will be instrumented by Istanbul)
      'scripts/metrics/*.js': sourcePreprocessors
    },

    // test results reporter to use
    // possible values: 'dots', 'progress'
    // available reporters: https://npmjs.org/browse/keyword/karma-reporter
    reporters: ['dots', 'coverage'],

    coverageReporter: {
      reporters : [
        {type: 'html', dir : 'coverage/metrics/'},
        {type: 'cobertura', dir: 'coverage/metrics/'},
        {type: 'lcovonly', dir: 'coverage/metrics/'}
      ]
    },

    // web server port
    port: 9876,


    // enable / disable colors in the output (reporters and logs)
    colors: true,


    // level of logging
    // possible values: config.LOG_DISABLE || config.LOG_ERROR || config.LOG_WARN || config.LOG_INFO || config.LOG_DEBUG
    logLevel: config.LOG_INFO,


    // enable / disable watching file and executing tests whenever any file changes
    autoWatch: true,


    // start these browsers
    // available browser launchers: https://npmjs.org/browse/keyword/karma-launcher
    browsers: ['Firefox'],


    // Continuous Integration mode
    // if true, Karma captures browsers, runs the tests and exits
    singleRun: true,

    // Concurrency level
    // how many browser should be started simultaneous
    concurrency: Infinity
  });
};
