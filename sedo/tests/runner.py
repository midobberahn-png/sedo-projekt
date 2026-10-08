"""Seed a synthetic company only in databases created by Django's test runner."""
import json

from django.db import connections
from django.db.models.signals import post_migrate
from django.test.runner import DiscoverRunner

from sedo.company_profile import PROFILE_KEY, validate_profile
from .company_fixtures import SYNTHETIC_PROFILE


class SyntheticCompanyRunner(DiscoverRunner):
    def setup_databases(self, **kwargs):
        aliases = kwargs.get('aliases')
        self._test_database_names = {}
        for alias in connections:
            if aliases is not None and alias not in aliases:
                continue
            connection = connections[alias]
            original = str(connection.settings_dict['NAME'])
            expected = str(connection.creation._get_test_db_name())
            if expected == original:
                raise RuntimeError('Synthetic fixtures require a separate test database.')
            self._test_database_names[alias] = expected
        self._seed_dispatch_uid = ('sedo-synthetic-company', id(self))
        post_migrate.connect(self._seed_company, weak=False, dispatch_uid=self._seed_dispatch_uid)
        try:
            return super().setup_databases(**kwargs)
        except BaseException:
            post_migrate.disconnect(dispatch_uid=self._seed_dispatch_uid)
            raise

    def _seed_company(self, sender, using, **kwargs):
        if getattr(sender, 'label', None) != 'sedo':
            return
        expected = self._test_database_names.get(using)
        if expected is None or str(connections[using].settings_dict['NAME']) != expected:
            return
        from sedo.models import AppSetting
        AppSetting.objects.using(using).update_or_create(
            key=PROFILE_KEY,
            defaults={'value': json.dumps(validate_profile(SYNTHETIC_PROFILE), sort_keys=True)},
        )

    def teardown_databases(self, old_config, **kwargs):
        post_migrate.disconnect(dispatch_uid=self._seed_dispatch_uid)
        return super().teardown_databases(old_config, **kwargs)
