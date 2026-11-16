"""Candidate selection for `kb tools visibility sweep`, against ephemeral SQLite.

The rule functions and the already-decided check are plain ORM queries, so
they run here with an explicit session. Writing decisions (--apply) goes
through record_visibility_decision, which needs Postgres and is covered by
test_document_visibility_postgres.py.
"""

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from kb_mcp.kb.db_models import Base, Document, RawDocument, Source
from kb_mcp.kb.tools import (
    SPREADSHEET_SOURCE_TYPES,
    VISIBILITY_RULES,
    _already_decided,
    _rule_oversized_spreadsheet,
    available_visibility_rules,
)
from kb_mcp.kb.visibility_rules import load_rules, parse_rules

XLSX = SPREADSHEET_SOURCE_TYPES[0]
PDF = "application/pdf"


@pytest.fixture()
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/sweep.db")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    s.add_all([Source(id="mu2e-docdb"), Source(id="mu2e-wiki")])
    yield s
    s.close()


def _add(session, raw_id, source_type, chars, source_id="mu2e-docdb", extra_docs=()):
    session.add(RawDocument(id=raw_id, source_id=source_id, source_type=source_type, content_hash=raw_id))
    session.add(Document(id=f"{raw_id}-doc", source_id=source_id, raw_document_id=raw_id,
                         source_type=source_type, text="x" * chars))
    for i, n in enumerate(extra_docs):
        session.add(Document(id=f"{raw_id}-child{i}", source_id=source_id, raw_document_id=raw_id,
                             source_type=source_type, doc_type="table", text="y" * n))
    session.flush()


def test_rule_is_registered():
    assert VISIBILITY_RULES["oversized-spreadsheet"] is _rule_oversized_spreadsheet


def test_selects_only_spreadsheets_over_the_threshold(session):
    _add(session, "big-xlsx", XLSX, 2_000)
    _add(session, "small-xlsx", XLSX, 500)
    _add(session, "big-pdf", PDF, 5_000)  # long reports are not the target
    got = _rule_oversized_spreadsheet(session, max_chars=1_000)
    assert [d["raw_document_id"] for d in got] == ["big-xlsx"]


def test_default_hides_from_search_only(session):
    _add(session, "big-xlsx", XLSX, 2_000)
    (d,) = _rule_oversized_spreadsheet(session, max_chars=1_000)
    assert d["visible_search"] is False
    assert d["visible_get"] is True
    assert d["meta"]["chars"] == 2_000 and d["meta"]["rule"] == "oversized-spreadsheet"


def test_hide_get_also_hides_from_get(session):
    _add(session, "big-xlsx", XLSX, 2_000)
    (d,) = _rule_oversized_spreadsheet(session, max_chars=1_000, hide_get=True)
    assert d["visible_get"] is False


def test_one_decision_per_raw_document_using_its_longest_text(session):
    """A raw file's extracted tables share its raw_document_id; it must
    still come out once, sized by its longest parsed text."""
    _add(session, "big-xlsx", XLSX, 500, extra_docs=(3_000, 200))
    got = _rule_oversized_spreadsheet(session, max_chars=1_000)
    assert len(got) == 1 and got[0]["meta"]["chars"] == 3_000


def test_source_id_restricts(session):
    _add(session, "docdb-xlsx", XLSX, 2_000, source_id="mu2e-docdb")
    _add(session, "wiki-xlsx", XLSX, 2_000, source_id="mu2e-wiki")
    got = _rule_oversized_spreadsheet(session, source_id="mu2e-wiki", max_chars=1_000)
    assert [d["raw_document_id"] for d in got] == ["wiki-xlsx"]


def test_already_decided_with_no_decisions(session):
    """Rows in document_visibility_decisions need a real Postgres sequence for
    seq, so only the empty cases run here; group scoping and the skip itself
    are in test_document_visibility_postgres.py."""
    assert _already_decided(session, set(), "*") == set()
    assert _already_decided(session, {"nothing-decided"}, "*") == set()


def _raw(session, raw_id, doc_id, source_id="mu2e-docdb", title=None):
    meta = {"title": title} if title else {}
    session.add(RawDocument(id=raw_id, source_id=source_id, doc_id=doc_id, source_type=PDF,
                            content_hash=raw_id, meta=meta))
    session.flush()


def _ids(decisions):
    return sorted(d["raw_document_id"] for d in decisions)


# --- the generic rule, from a small test configuration ---------------------

RULE = parse_rules({"pm": {
    "reason": "project report",
    "name_patterns": [r"%cost\_report%"],
    "entry_titles": ["Work Authorization%"],
    "packages": [{"titles": ["Monthly package%"], "file_patterns": ["%budget%"]}],
    "exclude_patterns": ["%bcr%"],
}})["pm"]


def test_name_patterns_select_anywhere_and_hide_from_search_only(session):
    _raw(session, "a", "100-Cost_Report_Jan")
    _raw(session, "b", "101-Costreport")          # \_ is a literal underscore
    (d,) = RULE.select(session)
    assert d["raw_document_id"] == "a"
    assert d["visible_search"] is False and d["visible_get"] is True
    assert d["reason"] == "project report" and d["meta"] == {"rule": "pm", "doc_id": "100-Cost_Report_Jan"}


def test_entry_titles_select_the_whole_entry(session):
    _raw(session, "a", "200-anything", title="Work Authorization Document 1")
    _raw(session, "b", "201-anything", title="Calorimeter work plan")
    assert _ids(RULE.select(session)) == ["a"]


def test_package_file_patterns_count_only_inside_a_package(session):
    _raw(session, "a", "300-Budget_Nov", title="Monthly package November")
    _raw(session, "b", "300-Gantt_Nov", title="Monthly package November")
    _raw(session, "c", "301-Trigger_timing_budget", title="Trigger timing")
    _raw(session, "d", "302-Budget_untitled")     # no title: not in a package
    assert _ids(RULE.select(session)) == ["a"]


def test_exclude_patterns_win_over_every_selector(session):
    _raw(session, "a", "400-BCR12_Cost_Report")
    _raw(session, "b", "401-BCR12_budget", title="Monthly package May")
    _raw(session, "c", "402-BCR13", title="Work Authorization Document 2")
    assert RULE.select(session) == []


def test_hide_get_comes_from_the_rule_or_the_sweep(session):
    _raw(session, "a", "100-Cost_Report_Jan")
    assert RULE.select(session, hide_get=True)[0]["visible_get"] is False
    strict = parse_rules({"x": {"name_patterns": ["%cost%"], "hide_get": True}})["x"]
    assert strict.select(session)[0]["visible_get"] is False


def test_source_id_restricts_a_configured_rule(session):
    _raw(session, "a", "100-Cost_Report_Jan", source_id="mu2e-docdb")
    _raw(session, "b", "100-Cost_Report_Jan", source_id="mu2e-wiki")
    assert _ids(RULE.select(session, source_id="mu2e-wiki")) == ["b"]


@pytest.mark.parametrize("spec, message", [
    ({"name_patterns": ["%x%"], "nmae_patterns": ["%y%"]}, "unknown key"),
    ({"reason": "nothing selected"}, "selects nothing"),
    ({"packages": [{"titles": ["T%"]}]}, "titles' and 'file_patterns'"),
    ({"name_patterns": "%x%", "exclude_patterns": [""]}, "non-empty strings"),
])
def test_mistakes_in_the_file_are_errors(spec, message):
    with pytest.raises(ValueError, match=message):
        parse_rules({"bad": spec})


def test_rules_file_comes_from_the_environment(tmp_path, monkeypatch):
    f = tmp_path / "privacy.toml"
    f.write_text("[visibility.rules.mine]\nname_patterns = ['%x%']\n")
    monkeypatch.setenv("PRIVACY_CONFIG_FILE", str(f))
    assert set(load_rules()) == {"mine"}
    assert set(available_visibility_rules()) == {"oversized-spreadsheet", "mine"}
    monkeypatch.setenv("PRIVACY_CONFIG_FILE", "")      # empty: none configured
    assert load_rules() == {}


# --- Mu2e's cost-report rule, from the shipped file ------------------------

MU2E_RULES = Path(__file__).resolve().parents[2] / "deploy" / "mu2e-privacy.toml"
CAM = "47502 Accelerator Systems CAM docs November 2025"


@pytest.fixture()
def cost_report():
    return load_rules(MU2E_RULES)["cost-report"].select


def test_cost_report_matches_the_monthly_report_file_names(session, cost_report):
    _raw(session, "a", "52501-Cost_Report_-_Apr25_-_47502.09_Target_Station")
    _raw(session, "b", "55795/Mu2e MONTHLY CAM Cost Report (Feb26) - BED Analysis")
    _raw(session, "c", "56278/Cost Report - 47502.01 Accel PM - Mar26")
    _raw(session, "d", "12345-CostEstimate_TrackerStations")   # not a cost report
    _raw(session, "e", "23456-Mu2e_Monthly_Report_July_2020")  # a monthly report, not a cost report
    _raw(session, "f", "33356-Mu2e_Earned_Value_202004_xlsx")
    _raw(session, "g", "58390-Final_Earned_Value_202608_pdf")
    _raw(session, "h", "54976-Mu2e_November_TAR_202511_-_Completed_-_Sent_to_PCS_-_v1")
    _raw(session, "i", "56278/Mu2e TAR - Mar26 - Status Complete - Final")
    _raw(session, "j", "54042-Mu2e_Turnaround_Report__2025-Sep_-_Complete_-_Verified_-_v1")
    _raw(session, "k", "58983-STM_Collimation_-_Nov_3_-_DocDB_58983_pptx")  # "target", "STAR": not TAR
    _raw(session, "l", "5197-EXtinctionMonitorGeometry")
    _raw(session, "m", "40001-StarterKit_tarball_notes")
    got = cost_report(session)
    assert _ids(got) == ["a", "b", "c", "f", "g", "h", "i", "j"]
    assert all(d["visible_search"] is False and d["visible_get"] is True for d in got)
    assert {d["meta"]["rule"] for d in got} == {"cost-report"}


def test_cost_report_matches_rates_indices_and_cost_impacts_anywhere(session, cost_report):
    _raw(session, "a", "44527-Hour_Rate_Report_-_Feb23")            # no title: never parsed
    _raw(session, "b", "51208-SPI_CPI_Trends_-_DRRF")
    _raw(session, "c", "4470-CR003_Mu2e_Cost_Impact_20140820_pdf")
    _raw(session, "d", "4053-TROC_SPI_Slow_Controls_pdf")          # the SPI bus, not an index
    assert _ids(cost_report(session)) == ["a", "b", "c"]


def test_cost_report_takes_money_and_people_out_of_a_cam_package(session, cost_report):
    for raw_id, name in [
        ("a", "54976-Purchase_Tracking_-_Mu2e_v2025-11"),
        ("b", "54976-Pruchases_-_Nov25"),
        ("c", "54976-Labor_Charges_-_Nov25-Final"),
        ("d", "54976-Budget_Forecast_-_Nov25_-_Materials"),
        ("e", "54976-AD_Buget_P6_Analysis"),
        ("f", "54976-Variance_Report_-_External_Beamline"),
        ("g", "54976-Deailed_EV_-_Nov25"),
        ("h", "54976-CAM_eToolbox_-_CPR_-_09_-_Target_Station"),
        ("i", "54976-Mu2e_active_CTCs"),
        ("j", "54976-Mu2e_Resources"),
        ("k", "54976-P6_Gantt_Chart_-_Nov25"),                      # schedule: stays
        ("l", "54976-Mu2e_Near_Critical_Activities"),               # schedule: stays
        ("m", "54976-BCR281_Approved"),                             # change request: stays
        ("n", "46557-Mu2e-BCR123_-__AC_Dipole_Labor_Scope"),        # change request about labor: stays
        ("o", "47085-Mu2e_0034_-_Extinction_Monitor_LOE_and_Stacked_Shielding_Procurement"),  # BCR: stays
    ]:
        _raw(session, raw_id, name, title=CAM)
    assert _ids(cost_report(session)) == list("abcdefghij")


def test_cost_report_leaves_generic_words_alone_outside_a_package(session, cost_report):
    _raw(session, "a", "15621-CRV_Aluminum_purchase_status_Feb_2018_pdf", title="Update on CRV Aluminum Purchase")
    _raw(session, "b", "12833-STM_-_Institutional_Division_of_Labor", title="Stopping Target Monitor Institutional Responsibilities")
    _raw(session, "c", "30001-Trigger_timing_budget", title="Trigger timing budget")
    _raw(session, "d", "23398-Preliminary_Float_Analysis_202610",
         title="Preliminary detailed EV Stoplight and schedule reports for additions/corrections")
    assert cost_report(session) == []


def test_cost_report_takes_work_authorizations_by_title(session, cost_report):
    _raw(session, "a", "5097-475.08.08_WAD_Schedule", title="Work Authorization Document for Control Account 475.08.08")
    _raw(session, "b", "5098-Something_else", title="Calorimeter work plan")
    assert _ids(cost_report(session)) == ["a"]
