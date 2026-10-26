from ppm.corpus.catalog import infer_category, infer_stage, parse_code, parse_title


def test_parse_code_and_title():
    assert parse_code("601 - Cost Monitoring Tool.xlsx") == "601"
    assert parse_title("601 - Cost Monitoring Tool.xlsx") == "Cost Monitoring Tool"
    assert parse_code("Appendix 8 Daily Construction Report.xlsx") is None
    assert parse_title("Appendix 8 Daily Construction Report.xlsx").startswith("Appendix 8")


def test_stage_from_folder_wins():
    assert infer_stage("4. Construction/406 - Agenda.doc", "Agenda") == "construction"
    assert infer_stage("5. Close-out/505 - Defect Register.xlsx", "Defect Register") == "closeout"
    assert infer_stage("2. Preconstruction/202 PEP Template.docx", "PEP Template") == "preconstruction"


def test_stage_from_title_when_flat():
    assert infer_stage("PM/PM", "Change Control Register Template") == "governance"
    assert infer_stage("PM/PM", "Practical Completion Certificate") == "closeout"
    assert infer_stage("PM/PM", "Procurement Strategy Dashboard") == "procurement"


def test_category_rules_are_specific_first():
    assert infer_category("Project Close Out Report") == "handover"
    assert infer_category("Monthly Cost Report") == "cost_report"
    assert infer_category("Risk Register Template") == "risk"
    assert infer_category("Project Cash Flow Document") == "cash_flow"
    assert infer_category("Something Unclassified") == "other"
