"""Tests for the south-jersey-creative-deal-finder intake CLI.

The Notion API is replaced by an in-memory fake, so no token or network is needed.

Run: python3 -m unittest discover -s skills/tests
"""

import io
import json
import os
import subprocess  # nosec B404
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent / "south-jersey-creative-deal-finder"
sys.path.insert(0, str(SKILL / "scripts"))

import deal_desk_intake as ddi  # noqa: E402

SKIP = """John Q Owner, age 61
(856) 555-0101 Wireless Verizon last seen Mar 2026
856.555.0199 Landline Comcast
john.owner@Example.com
Relatives: Mary Owner, Peter Owner
"""


def _pid(path):
    return path.split("/")[2]


class FakeNotion:

    """Just enough of the Notion API: data source query, page create/get/patch, block children."""

    def __init__(self, rows=None):
        """Seed the fake with existing Deals Pipeline rows (titles only)."""
        self.pages = {}
        self.children = {}
        self.calls = []
        for i, title in enumerate(rows or []):
            self.pages[f"page-{i}"] = {"Address": {"title": [{"plain_text": title}]}}
            self.children[f"page-{i}"] = []
        self.routes = [
            ("POST", lambda p: p.endswith("/query"), self._query),
            ("POST", lambda p: p == "/pages", self._create),
            ("GET", lambda p: p.startswith("/pages/"), self._get_page),
            ("PATCH", lambda p: p.startswith("/pages/"), self._patch_page),
            ("GET", lambda p: "/children" in p, self._get_children),
            ("PATCH", lambda p: "/children" in p, self._append_children),
        ]

    def __call__(self, method, path, body=None):
        """Stand in for deal_desk_intake._request: record the call and route it."""
        self.calls.append((method, path, body))
        for verb, matches, handler in self.routes:
            if verb == method and matches(path):
                return handler(path, body)
        raise AssertionError(f"unexpected call {method} {path}")

    def _query(self, _path, body):
        needle = body["filter"]["title"]["contains"].lower()
        hits = [{"id": pid, "properties": props} for pid, props in self.pages.items()
                if needle in props["Address"]["title"][0]["plain_text"].lower()]
        return {"results": hits, "has_more": False}

    def _create(self, _path, body):
        pid = f"page-{len(self.pages)}"
        props = json.loads(json.dumps(body["properties"]))
        props["Address"]["title"][0]["plain_text"] = props["Address"]["title"][0]["text"]["content"]
        self.pages[pid] = props
        self.children[pid] = list(body["children"])
        return {"id": pid, "url": f"https://notion.so/{pid}"}

    def _get_page(self, path, _body):
        pid = _pid(path)
        out = dict(self.pages[pid])
        notes = out.get("Notes", {}).get("rich_text", [])
        out["Notes"] = {"rich_text": [{"plain_text": t.get("plain_text", t.get("text", {}).get("content", ""))}
                                      for t in notes]}
        return {"id": pid, "properties": out}

    def _patch_page(self, path, body):
        pid = _pid(path)
        self.pages[pid].update({k: v for k, v in body["properties"].items() if k != "Address"})
        return {"id": pid}

    def _get_children(self, path, _body):
        res = [{"type": b["type"], b["type"]: {"rich_text": [
            {"plain_text": t["text"]["content"]} for t in b[b["type"]]["rich_text"]]}}
            for b in self.children[_pid(path)]]
        return {"results": res, "has_more": False}

    def _append_children(self, path, body):
        self.children[_pid(path)].extend(body["children"])
        return {}

    def appended_dossiers(self, pid):
        """Count how many intake dossiers were written to a page."""
        return sum(1 for b in self.children[pid] if b["type"] == "heading_3"
                   and b["heading_3"]["rich_text"][0]["text"]["content"].startswith("Intake dossier"))


def run(argv, fake):
    """Run one intake against the fake Notion and return (deal, result)."""
    with mock.patch.object(ddi, "_request", fake):
        args = ddi.parse_args(argv)
        deal, contacts = ddi.build_deal(args)
        return deal, ddi.upsert_deal(deal, contacts, dry_run=args.dry_run)


class TestHelpers(unittest.TestCase):

    """Pure helpers: addresses, skip-trace parsing, deal math."""

    def test_normalize_street(self):
        """Street spellings normalize to the same dedupe key."""
        self.assertEqual(ddi.normalize_street("100 Example Road, Sewell NJ"), "100 example rd")
        self.assertEqual(ddi.normalize_street("321 N. Example Dr."), "321 n example dr")
        self.assertEqual(ddi.normalize_street("17 West Sample Avenue"), ddi.normalize_street("17 W Sample Ave"))

    def test_skip_trace(self):
        """Phones, types, carriers, emails and relatives are parsed and marked unverified."""
        c = ddi.parse_skip_trace(SKIP)
        self.assertEqual(c["primary_phone"], "(856) 555-0101")
        self.assertEqual(c["phones"][0]["type"], "wireless")
        self.assertEqual(c["phones"][0]["carrier"], "Verizon")
        self.assertEqual(c["phones"][0]["recency"], "Mar 2026")
        self.assertEqual(c["secondary_phones"], ["(856) 555-0199"])
        self.assertEqual(c["emails"], ["john.owner@example.com"])
        self.assertEqual(c["related_people"], ["Mary Owner", "Peter Owner"])
        self.assertFalse(c["verified"])
        self.assertFalse(c["dnc_checked"])

    def test_assumable_and_gap(self):
        """Assumable rule and the cash-plus-carryback equity gap split."""
        self.assertTrue(ddi.assess_assumable("fha", 2.75, True))
        self.assertFalse(ddi.assess_assumable("FHA", 3.25, True))
        self.assertFalse(ddi.assess_assumable("Conventional", 2.5, True))
        self.assertFalse(ddi.assess_assumable("VA", 2.5, False))
        self.assertIsNone(ddi.assess_assumable(None, 2.5, True))
        self.assertEqual(ddi.equity_gap_plan(240000, 205000),
                         {"equity_gap": 35000.0, "cash": 10000.0, "seller_carryback_2nd": 25000.0})
        self.assertEqual(ddi.equity_gap_plan(100, 200)["equity_gap"], 0.0)

    def test_rich_text_chunked(self):
        """Long text is split at Notion's 2000-character limit."""
        self.assertEqual([len(c["text"]["content"]) for c in ddi._rt("x" * 4500)], [2000, 2000, 500])

    def test_bad_number(self):
        """A non-numeric amount raises IntakeError."""
        with self.assertRaises(ddi.IntakeError):
            ddi._num("about 200k")


class TestIntake(unittest.TestCase):

    """End-to-end intake runs against the fake Notion."""

    def setUp(self):
        """Clear the operator env var so each test sets its own."""
        os.environ.pop("DEAL_DESK_OPERATOR", None)

    def test_dry_run_writes_nothing(self):
        """A dry run makes no Notion calls and lists missing fields."""
        fake = FakeNotion()
        _, res = run(["--address", "1 Test St, Glassboro, NJ", "--dry-run"], fake)
        self.assertEqual(res["action"], "dry-run")
        self.assertEqual(fake.calls, [])
        self.assertIn("Owner of record", " ".join(res["missing"]))

    def test_create_then_idempotent_rerun(self):
        """A rerun with the same facts updates the row without a second dossier."""
        fake = FakeNotion()
        argv = ["--address", "12 Maple Ave, Glassboro, NJ", "--operator", "Fred",
                "--skip-text", SKIP, "--asking", "240000", "--loan-type", "FHA", "--rate", "2.75",
                "--loan-balance", "205000"]
        _, res = run(argv, fake)
        self.assertEqual(res["action"], "created")
        pid = res["page_id"]
        props = fake.pages[pid]
        self.assertEqual(props["Strategy"]["select"]["name"], "Assumable")
        self.assertEqual(props["Equity gap"]["number"], 35000.0)
        self.assertEqual(props["Owner phone"]["phone_number"], "(856) 555-0101")
        self.assertEqual(props["Owner email"]["email"], "john.owner@example.com")
        self.assertIn("by Fred", props["Notes"]["rich_text"][0]["text"]["content"])
        self.assertNotIn("Attorney first", props)
        _, res2 = run(argv, fake)
        self.assertEqual(res2["action"], "updated")
        self.assertEqual(res2["dossier"], "unchanged (already appended)")
        self.assertEqual(len(fake.pages), 1)
        self.assertEqual(fake.appended_dossiers(pid), 1)

    def test_dedupe_matches_spelling_variants(self):
        """A "Dr" address matches an existing "Drive" row, not a different house number."""
        fake = FakeNotion(rows=["487 Example Drive", "48 Example Dr"])
        _, res = run(["--address", "487 Example Dr, Sewell, NJ 08080", "--asking", "199000"], fake)
        self.assertEqual((res["action"], res["page_id"]), ("updated", "page-0"))
        self.assertEqual(len(fake.pages), 2)

    def test_update_keeps_existing_pipeline_values(self):
        """Defaults never overwrite stage, strategy, attorney flag or notes."""
        fake = FakeNotion(rows=["9 Oak Ln"])
        fake.pages["page-0"].update({
            "Stage": {"select": {"name": "5 Negotiating"}},
            "Strategy": {"select": {"name": "Lease Option"}},
            "Attorney first": {"checkbox": True},
            "Notes": {"rich_text": [{"text": {"content": "Seller wants 60 days."}}]},
        })
        run(["--address", "9 Oak Lane", "--owner-name", "Jane Roe"], fake)
        props = fake.pages["page-0"]
        self.assertEqual(props["Stage"]["select"]["name"], "5 Negotiating")
        self.assertEqual(props["Strategy"]["select"]["name"], "Lease Option")
        self.assertTrue(props["Attorney first"]["checkbox"])
        self.assertEqual(props["Owner name"]["rich_text"][0]["text"]["content"], "Jane Roe")
        notes = props["Notes"]["rich_text"][0]["text"]["content"]
        self.assertIn("Seller wants 60 days.", notes)
        self.assertTrue(notes.startswith("[intake "))

    def test_explicit_values_do_update(self):
        """Values the operator passes do overwrite the row."""
        fake = FakeNotion(rows=["9 Oak Ln"])
        fake.pages["page-0"]["Stage"] = {"select": {"name": "1 Lead"}}
        run(["--address", "9 Oak Ln", "--stage", "3 Analyzing", "--strategy", "Flip"], fake)
        self.assertEqual(fake.pages["page-0"]["Stage"]["select"]["name"], "3 Analyzing")
        self.assertEqual(fake.pages["page-0"]["Strategy"]["select"]["name"], "Flip")

    def test_foreclosure_flags_and_holds_pitch(self):
        """--foreclosure adds the NJ warning and holds the pitch."""
        fake = FakeNotion()
        _, res = run(["--address", "5 Elm St, Paulsboro, NJ", "--foreclosure", "--skip-text", SKIP], fake)
        props = fake.pages[res["page_id"]]
        self.assertTrue(props["Attorney first"]["checkbox"])
        self.assertEqual(props["Lead source"]["select"]["name"], "Auction / sheriff")
        self.assertIn("ATTORNEY FIRST", props["Next action"]["rich_text"][0]["text"]["content"])
        blocks = fake.children[res["page_id"]]
        self.assertEqual(blocks[0]["type"], "callout")
        self.assertIn("46:10B-53", blocks[0]["callout"]["rich_text"][0]["text"]["content"])
        text = json.dumps(blocks)
        self.assertIn("Pitch on hold", text)
        self.assertNotIn("I'm a local buyer", text)

    def test_operator_from_env(self):
        """DEAL_DESK_OPERATOR sets the operator."""
        with mock.patch.dict(os.environ, {"DEAL_DESK_OPERATOR": "Fred"}):
            deal, _ = run(["--address", "1 Test St", "--dry-run"], FakeNotion())
        self.assertEqual(deal["operator"], "Fred")

    def test_facts_file_and_flags_win(self):
        """A --facts file fills fields and flags override it."""
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump({"owner_name": "From File", "annual_taxes": "6,123", "town": "Glassboro"}, fh)
        try:
            deal, _ = run(["--address", "1 Test St", "--facts", fh.name, "--town", "Pitman", "--dry-run"],
                          FakeNotion())
        finally:
            os.unlink(fh.name)
        self.assertEqual(deal["owner_name"], "From File")
        self.assertEqual(deal["annual_taxes"], 6123.0)
        self.assertEqual(deal["town"], "Pitman")

    def test_cli_rejects_bad_strategy_and_missing_token(self):
        """The launcher exits 2 on a bad strategy or a missing token."""
        env = {k: v for k, v in os.environ.items() if k != "NOTION_TOKEN"}
        launcher = SKILL / "bin" / "deal-desk-intake"
        bad = subprocess.run([str(launcher), "--address", "1 X St", "--strategy", "Wholesale"],  # nosec B603
                             capture_output=True, text=True, env=env, check=False)
        self.assertEqual(bad.returncode, 2)
        self.assertIn("invalid choice", bad.stderr)
        no_token = subprocess.run([str(launcher), "--address", "1 X St"],  # nosec B603
                                  capture_output=True, text=True, env=env, check=False)
        self.assertEqual(no_token.returncode, 2)
        self.assertIn("NOTION_TOKEN is not set", no_token.stderr)

    def test_main_dry_run_prints_json(self):
        """main() prints a JSON dry-run result and returns 0."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = ddi.main(["--address", "1 Test St", "--dry-run"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(buf.getvalue())["action"], "dry-run")


if __name__ == "__main__":
    unittest.main()
