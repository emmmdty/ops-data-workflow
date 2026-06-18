from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ops_data_workflow.recap_settings import get_recap_settings, update_recap_settings


class RecapSettingsTests(unittest.TestCase):
    def test_recap_weights_have_defaults_and_persist_until_updated(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workflow.sqlite3"

            initial = get_recap_settings(db_path)
            self.assertEqual(initial.activation_weight, 1.0)
            self.assertEqual(initial.first_pay_weight, 1.0)

            updated = update_recap_settings(db_path, activation_weight=3.5, first_pay_weight=11.0)
            loaded = get_recap_settings(db_path)

            self.assertEqual(updated.activation_weight, 3.5)
            self.assertEqual(updated.first_pay_weight, 11.0)
            self.assertEqual(loaded.activation_weight, 3.5)
            self.assertEqual(loaded.first_pay_weight, 11.0)
            self.assertTrue(loaded.updated_at)


if __name__ == "__main__":
    unittest.main()
