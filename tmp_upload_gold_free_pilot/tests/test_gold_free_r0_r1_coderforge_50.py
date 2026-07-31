import json
import tempfile
import unittest
from pathlib import Path


def make_card(card_id, split, identity, source_id="coderforge_verified_eval", agent_system_id="openhands_coderforge"):
    return {
        "card_id": card_id,
        "experiment_split": split,
        "source": {
            "source_id": source_id,
            "agent_system_id": agent_system_id,
        },
        "split_assignment": {
            "identity_key_sha256": identity,
        },
        "task_identity": {
            "trajectory_id": f"{card_id}_traj",
            "problem_statement": f"Task for {card_id}",
        },
        "hard_evidence_refs": {
            "patch": [{"path": "output_patch", "sha256": "patchhash"}],
            "test_or_reward": [{"path": "test_output", "sha256": "testhash"}],
            "tool_observations": [{"path": "agent_log", "sha256": "loghash"}],
        },
        "trajectory_refs": [{"path": "messages", "sha256": "messageshash"}],
        "source_payload": {
            "messages": [{"role": "assistant", "content": "I will inspect the failure."}],
            "agent_log": "tool output says pytest failed",
            "output_patch": "diff --git a/a.py b/a.py\n+pass\n",
            "test_output": "FAILED tests/test_a.py",
            "reward": 0.0,
        },
        "label_policy": {
            "no_gold_labels_generated": True,
            "no_llm_judge_called": True,
            "source_hard_evidence_only": True,
        },
    }


class GoldFreeR0R1CoderForge50Tests(unittest.TestCase):
    def test_selects_stable_coderforge_30_10_10_without_cross_split_identity(self):
        from scripts.run_gold_free_r0_r1_coderforge_50 import select_pilot_cards

        cards = []
        for split, count in [("train", 35), ("dev", 12), ("test", 11)]:
            for index in range(count):
                cards.append(make_card(f"{split}-{index:03d}", split, f"{split}-identity-{index:03d}"))
        cards.append(make_card("other-source", "train", "other", source_id="swegym_openhands_sampled"))
        cards.append(make_card("other-agent", "dev", "other-agent", agent_system_id="openhands_swegym"))

        selected = select_pilot_cards(cards, train_n=30, dev_n=10, test_n=10)

        self.assertEqual([len(selected[name]) for name in ["train", "dev", "test"]], [30, 10, 10])
        self.assertEqual(selected["train"][0]["card_id"], "train-000")
        self.assertEqual(selected["train"][-1]["card_id"], "train-029")
        self.assertTrue(
            all(card["source"]["source_id"] == "coderforge_verified_eval" for rows in selected.values() for card in rows)
        )
        train_ids = {card["split_assignment"]["identity_key_sha256"] for card in selected["train"]}
        dev_ids = {card["split_assignment"]["identity_key_sha256"] for card in selected["dev"]}
        test_ids = {card["split_assignment"]["identity_key_sha256"] for card in selected["test"]}
        self.assertFalse(train_ids & dev_ids)
        self.assertFalse(train_ids & test_ids)
        self.assertFalse(dev_ids & test_ids)

    def test_select_rejects_identity_leakage_across_requested_splits(self):
        from scripts.run_gold_free_r0_r1_coderforge_50 import select_pilot_cards

        cards = []
        for index in range(30):
            identity = "shared" if index == 0 else f"train-{index}"
            cards.append(make_card(f"train-{index:03d}", "train", identity))
        for index in range(10):
            identity = "shared" if index == 0 else f"dev-{index}"
            cards.append(make_card(f"dev-{index:03d}", "dev", identity))
        for index in range(10):
            cards.append(make_card(f"test-{index:03d}", "test", f"test-{index}"))

        with self.assertRaisesRegex(ValueError, "identity"):
            select_pilot_cards(cards, train_n=30, dev_n=10, test_n=10)

    def test_hard_checks_validate_evidence_refs_and_reject_outcome_as_gold(self):
        from scripts.run_gold_free_r0_r1_coderforge_50 import hard_check_detections

        card = make_card("train-001", "train", "identity-001")
        good_detection = {
            "card_id": "train-001",
            "trajectory_id": "train-001_traj",
            "has_hallucination": True,
            "detected_type": "completion_progress_claim",
            "unsupported_unit": "Agent claimed tests were safe despite failing log.",
            "evidence_ids": ["trajectory:messages", "tool_observations:agent_log", "test_or_reward:test_output"],
            "support_status": "contradicted",
            "why_not_merely_a_bug": "The issue is the unsupported completion claim, not failure alone.",
        }
        bad_detection = {
            "card_id": "train-001",
            "trajectory_id": "train-001_traj",
            "has_hallucination": True,
            "detected_type": "patch_effect_behavior",
            "unsupported_unit": "reward is zero",
            "evidence_ids": ["test_or_reward:reward", "missing:path"],
            "support_status": "unsupported",
            "why_not_merely_a_bug": "",
        }

        checks = hard_check_detections([good_detection, bad_detection], {"train-001": card})

        self.assertEqual(checks["detections"], 2)
        self.assertEqual(checks["json_schema_valid"], 2)
        self.assertEqual(checks["evidence_ref_valid"], 1)
        self.assertEqual(checks["outcome_as_gold_violations"], 1)
        self.assertEqual(checks["ordinary_failure_boundary_present"], 1)

    def test_hard_checks_do_not_treat_plain_resolved_word_as_outcome_gold(self):
        from scripts.run_gold_free_r0_r1_coderforge_50 import hard_check_detections

        card = make_card("train-002", "train", "identity-002")
        detection = {
            "card_id": "train-002",
            "trajectory_id": "train-002_traj",
            "has_hallucination": True,
            "detected_type": "completion_progress_claim",
            "unsupported_unit": "Agent claimed the issue was resolved without inspecting the failure.",
            "evidence_ids": ["trajectory:messages", "tool_observations:agent_log"],
            "support_status": "contradicted",
            "why_not_merely_a_bug": "The problem is the unsupported completion claim, not the failed patch alone.",
        }

        checks = hard_check_detections([detection], {"train-002": card})

        self.assertEqual(checks["outcome_as_gold_violations"], 0)

    def test_parse_args_defaults_to_15_api_concurrency(self):
        from scripts.run_gold_free_r0_r1_coderforge_50 import parse_args

        args = parse_args(["--dry-run"])

        self.assertEqual(args.api_concurrency, 15)

    def test_load_cards_can_reuse_preselected_cards_root(self):
        from scripts.run_gold_free_r0_r1_coderforge_50 import load_cards, parse_args, select_pilot_cards

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            for split, count in [("train", 30), ("dev", 10), ("test", 10)]:
                path = root / f"selected_cards_{split}.jsonl"
                with path.open("w", encoding="utf-8") as handle:
                    for index in range(count):
                        card = make_card(f"{split}-{index:03d}", split, f"{split}-identity-{index:03d}")
                        handle.write(json.dumps(card) + "\n")

            args = parse_args(["--dry-run", "--selected-cards-root", str(root)])
            cards = load_cards(args)
            selected = select_pilot_cards(cards, train_n=30, dev_n=10, test_n=10)

        self.assertEqual(len(cards), 50)
        self.assertEqual([len(selected[name]) for name in ["train", "dev", "test"]], [30, 10, 10])


if __name__ == "__main__":
    unittest.main()
