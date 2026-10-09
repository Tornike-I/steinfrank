import unittest

import capability

IDX = [{"key": "voip-engineer", "title": "VoIP Engineer", "aliases": ["voip engineer", "voice over ip", "voip", "sip engineer"]},
       {"key": "ux-ui-designer", "title": "UX/UI Designer", "aliases": ["ux/ui designer", "ux/ui", "ui/ux"]},
       {"key": "qa-engineer", "title": "QA Engineer", "aliases": ["qa engineer", "tester", "qa"]},
       {"key": "devops-engineer", "title": "DevOps Engineer", "aliases": ["devops"]}]
CTX = {"today": "2026-10-09", "lang": "en", "knowledge_index": IDX}


def pack(title="VoIP Engineer", n=6):
    topics = [{"name": f"Topic {t}", "questions": [{"q": f"{title} question {t}-{lv}-{i} about routing", "level": lv, "kind": "concept",
                                                    "answer_hint": "hint"} for lv in ("junior", "mid", "senior", "lead") for i in range(n)]}
              for t in range(3)]
    return {"key": "k", "version": 1, "source": "curated", "title": title, "competencies": ["SIP"], "topics": topics,
            "exercises": [{"level": "senior", "title": "Design HA"}], "rubric": ["depth"], "recommendations": ["practice"]}


class ParseTests(unittest.TestCase):
    def test_voip_uppercase_senior(self):
        p = capability.parse_request("Prepare me to SENIOR VOIP interview", CTX)
        self.assertEqual((p["role"], p["role_key"], p["seniority"]), ("VoIP Engineer", "voip-engineer", "senior"))

    def test_ux_ui_with_space(self):
        self.assertEqual(capability.parse_request("Prepare me to UX/ UI interview", CTX)["role_key"], "ux-ui-designer")

    def test_unknown_role_is_preserved_not_general_it(self):
        p = capability.parse_request("Prepare me to SWIFT interview", CTX)
        self.assertEqual((p["role"], p["role_key"]), ("swift", None))

    def test_seniority_words_removed_from_unknown_role(self):
        self.assertEqual(capability.parse_request("Prepare me to java senior developer interview", CTX)["role"], "java developer")

    def test_sre_is_not_cut_by_sr(self):
        self.assertEqual(capability.parse_request("Prepare me for an SRE interview", CTX)["role"], "sre")

    def test_count_and_answers(self):
        p = capability.parse_request("Give me 20 questions with answers for a junior QA interview", CTX)
        self.assertEqual((p["question_count"], p["include_answers"], p["seniority"], p["role_key"]), (20, True, "junior", "qa-engineer"))

    def test_czech_and_russian(self):
        self.assertEqual(capability.parse_request("Připrav mě na pohovor na pozici tester", CTX)["role_key"], "qa-engineer")
        self.assertEqual(capability.parse_request("Подготовь меня к собеседованию на DevOps инженера", CTX)["role_key"], "devops-engineer")

    def test_missing_role_asks(self):
        p = capability.parse_request("Prepare me for an interview", CTX)
        self.assertEqual((p["_missing"], p["seniority"]), ("role", "mid"))

    def test_other_request(self):
        self.assertIsNone(capability.parse_request("What's the weather in Prague?", CTX))


class RunTests(unittest.TestCase):
    def test_senior_prefers_senior_questions(self):
        r = capability.run({"role": "VoIP Engineer", "seniority": "senior", "question_count": 12, "knowledge": pack()})
        levels = [q["level"] for s in r["sections"] for q in s["questions"]]
        self.assertEqual(len(levels), 12)
        self.assertTrue(all(lv == "senior" for lv in levels))

    def test_topics_are_covered_round_robin(self):
        r = capability.run({"role": "x", "seniority": "junior", "question_count": 6, "knowledge": pack()})
        self.assertEqual(len(r["sections"]), 3)

    def test_shortfall_reported(self):
        r = capability.run({"role": "x", "seniority": "junior", "question_count": 40, "knowledge": pack(n=1)})
        self.assertGreater(r["shortfall"], 0)

    def test_different_packs_give_different_content(self):
        a = capability.format_result(capability.run({"role": "a", "seniority": "mid", "knowledge": pack("VoIP Engineer")}), "en")
        b = capability.format_result(capability.run({"role": "b", "seniority": "mid", "knowledge": pack("UX/UI Designer")}), "en")
        self.assertNotEqual(a, b)
        self.assertIn("### VoIP Engineer", a)

    def test_missing_knowledge_is_honest(self):
        with self.assertRaises(ValueError):
            capability.run({"role": "x", "seniority": "mid"})

    def test_invalid_inputs(self):
        with self.assertRaises(ValueError):
            capability.run({"role": "", "knowledge": pack()})
        with self.assertRaises(ValueError):
            capability.run({"role": "x", "seniority": "guru", "knowledge": pack()})
        with self.assertRaises(ValueError):
            capability.run({"role": "x", "question_count": 400, "knowledge": pack()})

    def test_answers_only_when_asked(self):
        r = capability.run({"role": "x", "include_answers": True, "knowledge": pack()})
        self.assertIn("answer_hint", r["sections"][0]["questions"][0])
        self.assertIn("Key points", capability.format_result(r, "en"))

    def test_check_flags_generic_filler(self):
        bad = {"seniority": "mid", "role": "general IT", "sections": [{"topic": "t", "questions": [{"q": "What is Git?"}, {"q": "Process and a thread?"}]}]}
        self.assertTrue(capability.check_result(bad, {"seniority": "mid"}))
        good = capability.run({"role": "x", "seniority": "mid", "knowledge": pack()})
        self.assertEqual(capability.check_result(good, {"seniority": "mid"}), [])

    def test_research_section_shown_when_present(self):
        kn = {**pack(), "research": {"points": ["SIP over TLS is now expected"], "sources": ["https://example.org/jobs"],
                                     "fetched_at": "2026-10-09T10:00:00+00:00"}}
        s = capability.format_result(capability.run({"role": "x", "knowledge": kn}), "en")
        self.assertIn("Current market notes (researched 2026-10-09)", s)
        self.assertIn("SIP over TLS", s)

    def test_czech_labels(self):
        self.assertIn("Otázky na pohovor", capability.format_result(capability.run({"role": "x", "knowledge": pack()}), "cs"))


if __name__ == "__main__":
    unittest.main()
