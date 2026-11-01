"""Reporte de resultados del runner aislado; no cambia los tests ni sus aserciones."""
import json
import os
from pathlib import Path
import unittest
from django.test.runner import DiscoverRunner


class Result(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.passed = []

    def addSuccess(self, test):
        super().addSuccess(test)
        self.passed.append(test.id())

    def stopTestRun(self):
        super().stopTestRun()
        path = os.environ.get('BELLIAZZI_TEST_RESULTS')
        if path:
            Path(path).write_text(json.dumps({
                'executed': self.testsRun, 'passed': self.passed,
                'failures': [(test.id(), text) for test, text in self.failures],
                'errors': [(test.id(), text) for test, text in self.errors],
                'skipped': [(test.id(), reason) for test, reason in self.skipped],
                'expected_failures': [(test.id(), text) for test, text in self.expectedFailures],
                'unexpected_successes': [test.id() for test in self.unexpectedSuccesses],
            }, ensure_ascii=False, indent=2), encoding='utf8')


class Runner(unittest.TextTestRunner):
    resultclass = Result


class IsolatedTestRunner(DiscoverRunner):
    test_runner = Runner
