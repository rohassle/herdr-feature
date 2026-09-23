import unittest

from .helpers import SRC  # noqa: F401  (sets sys.path)
from herdr_feature import names


class NameRules(unittest.TestCase):
    def test_accepts_ticket_style_names(self):
        self.assertIsNone(names.validate_name("pay-1234-retry.v2_x"))

    def test_rejects_bad_names(self):
        for bad in ("", "-leading", "a/b", "a..b", "x.lock", "a" * 81, "with space", ".hidden"):
            self.assertIsNotNone(names.validate_name(bad), bad)

    def test_branch_and_folder(self):
        self.assertEqual(names.branch_for("rh/", "feat", None), "rh/feat")
        self.assertEqual(names.branch_for("", "feat", "api"), "feat-api")
        self.assertEqual(names.folder_for("repo", None), "repo")
        self.assertEqual(names.folder_for("repo", "api"), "repo@api")

    def test_same_name_is_case_insensitive(self):
        self.assertTrue(names.same_name("Foo", "foo"))
        self.assertFalse(names.same_name("foo", "bar"))

    def test_check_ref_format(self):
        self.assertIsNone(names.check_ref_format("feat/x-1"))
        self.assertIsNotNone(names.check_ref_format("feat//x"))
        self.assertIsNotNone(names.check_ref_format("x.lock"))
