"""
This settings file exists only to install the tests.telescope
app during testing.

To run tests, call: `./manage.py test --settings=project.settings_test`
"""
from project.settings import *  # noqa: inherit everything


INSTALLED_APPS = [
    *INSTALLED_APPS,
    "tracet.tests.telescope",
]