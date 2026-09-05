import unittest

from core import laser_material_safety as safety


class LaserMaterialSafetyTests(unittest.TestCase):
    def test_soft_card_requires_clarify(self):
        for label in ("软卡", "白色软卡", "软卡片"):
            with self.subTest(label=label):
                decision = safety.evaluate_material_safety(label)
                self.assertEqual(decision["decision"], "clarify")
                self.assertEqual(decision["reason"], "soft_card_ambiguous")
                self.assertIn("纸质", decision["message"])

    def test_paper_qualified_soft_card_is_not_soft_card_ambiguous(self):
        """用户已澄清为纸质后，不得再因“软卡”二字返回 soft_card_ambiguous。"""
        for label in ("纸质软卡", "纸质软卡片", "纸质 软卡"):
            with self.subTest(label=label):
                decision = safety.evaluate_material_safety(label)
                self.assertEqual(decision["decision"], "allow", decision)
                self.assertNotEqual(decision["reason"], "soft_card_ambiguous")
                self.assertEqual(decision["reason"], "allowed")

    def test_plastic_soft_card_still_clarify(self):
        for label in ("塑料软卡", "塑料软卡片", "塑胶软卡"):
            with self.subTest(label=label):
                decision = safety.evaluate_material_safety(label)
                self.assertEqual(decision["decision"], "clarify", decision)
                self.assertIn(decision["reason"], {"soft_card_ambiguous", "ambiguous_plastic"})
                self.assertNotEqual(decision["decision"], "allow")

    def test_pvc_priority_over_soft_card(self):
        """明确 PVC 必须 block，优先级高于软卡分类（不可先 clarify）。"""
        for label in ("PVC软卡", "聚氯乙烯软卡", "pvc 软卡", "PVC软卡片"):
            with self.subTest(label=label):
                decision = safety.evaluate_material_safety(label)
                self.assertEqual(decision["decision"], "block", decision)
                self.assertEqual(decision["reason"], "pvc_or_hazardous_plastic")

    def test_pvc_and_hazardous_plastic_block(self):
        for label in ("PVC", "pvc板", "聚氯乙烯", "黑色 PVC 片", "氯乙烯"):
            with self.subTest(label=label):
                decision = safety.evaluate_material_safety(label)
                self.assertEqual(decision["decision"], "block")
                self.assertEqual(decision["reason"], "pvc_or_hazardous_plastic")

    def test_ambiguous_plastic_clarify(self):
        for label in ("塑料", "普通塑料", "不明塑料", "plastic"):
            with self.subTest(label=label):
                decision = safety.evaluate_material_safety(label)
                self.assertEqual(decision["decision"], "clarify")
                self.assertEqual(decision["reason"], "ambiguous_plastic")

    def test_known_safe_material_allow(self):
        for label in ("椴木", "卡纸", "牛皮纸", "亚克力", "竹子"):
            with self.subTest(label=label):
                decision = safety.evaluate_material_safety(label)
                self.assertEqual(decision["decision"], "allow")

    def test_priority_matrix_table(self):
        """审查要求的完整优先级矩阵（allow 仅表示安全门不拦截）。"""
        matrix = (
            ("软卡", "clarify", "soft_card_ambiguous"),
            ("白色软卡", "clarify", "soft_card_ambiguous"),
            ("纸质软卡", "allow", "allowed"),
            ("纸质软卡片", "allow", "allowed"),
            ("塑料软卡", "clarify", None),  # reason may be soft_card or plastic
            ("PVC软卡", "block", "pvc_or_hazardous_plastic"),
            ("聚氯乙烯软卡", "block", "pvc_or_hazardous_plastic"),
            ("pvc板", "block", "pvc_or_hazardous_plastic"),
            ("塑料", "clarify", "ambiguous_plastic"),
            ("不明塑料", "clarify", "ambiguous_plastic"),
            ("椴木", "allow", "allowed"),
            ("卡纸", "allow", "allowed"),
            ("牛皮纸", "allow", "allowed"),
        )
        for label, decision, reason in matrix:
            with self.subTest(label=label):
                result = safety.evaluate_material_safety(label)
                self.assertEqual(result["decision"], decision, result)
                if reason is not None:
                    self.assertEqual(result["reason"], reason, result)
                if decision == "clarify" and label == "塑料软卡":
                    self.assertIn(result["reason"], {"soft_card_ambiguous", "ambiguous_plastic"})

    def test_strip_unconfirmed_manual_params(self):
        raw = {
            "material": "椴木",
            "power_percent": 55,
            "feed_rate": 1200,
            "passes": 2,
            "pixel_size_mm": 0.08,
            "threshold": -1,
            "frequency": 1000,
            "width_mm": 30,
            "manual_params_confirmed": False,
        }
        cleaned = safety.strip_unconfirmed_manual_params(raw)
        self.assertEqual(cleaned["material"], "椴木")
        self.assertEqual(cleaned["width_mm"], 30)
        for key in safety.MANUAL_PARAM_KEYS:
            self.assertNotIn(key, cleaned)
        self.assertNotIn("manual_params_confirmed", cleaned)

    def test_confirmed_manual_params_kept(self):
        raw = {
            "power_percent": 55,
            "feed_rate": 1200,
            "passes": 2,
            "manual_params_confirmed": True,
        }
        cleaned = safety.strip_unconfirmed_manual_params(raw)
        self.assertTrue(cleaned["manual_params_confirmed"])
        self.assertEqual(cleaned["power_percent"], 55)
        self.assertEqual(cleaned["feed_rate"], 1200)
        self.assertEqual(cleaned["passes"], 2)

    def test_identity_change_clears_sticky_manual_confirmation(self):
        previous = {
            "material": "椴木",
            "thickness_mm": 3,
            "laser_mode": "engrave",
            "power_percent": 55,
            "feed_rate": 1200,
            "manual_params_confirmed": True,
        }
        updated = safety.apply_manual_param_policy(
            previous,
            {"material": "卡纸", "thickness_mm": 1},
        )
        self.assertEqual(updated["material"], "卡纸")
        self.assertEqual(float(updated["thickness_mm"]), 1.0)
        self.assertNotIn("manual_params_confirmed", updated)
        self.assertNotIn("power_percent", updated)
        self.assertNotIn("feed_rate", updated)

    def test_identity_change_with_fresh_confirmation_keeps_new_manuals(self):
        previous = {
            "material": "椴木",
            "thickness_mm": 3,
            "power_percent": 55,
            "manual_params_confirmed": True,
        }
        updated = safety.apply_manual_param_policy(
            previous,
            {
                "material": "卡纸",
                "thickness_mm": 1,
                "power_percent": 30,
                "manual_params_confirmed": True,
            },
        )
        self.assertEqual(updated["material"], "卡纸")
        self.assertTrue(updated["manual_params_confirmed"])
        self.assertEqual(updated["power_percent"], 30)


if __name__ == "__main__":
    unittest.main()
