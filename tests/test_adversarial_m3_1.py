"""
tests/test_adversarial_m3_1.py - Adversarial Stress & Vulnerability Test Suite for Milestone 3.
Authored by challenger_m3_1.
Empirically tests edge cases, malformed structures, delimiter nesting, string escaping,
and corruption resilience for studio/verification/syntax_validators.py and studio/verification/tscn_validator.py.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import List

from studio.core.models import DiagnosticError
from studio.engine.ast_models import (
    ConnectionAST,
    ExtResourceAST,
    NodeAST,
    SceneAST,
    SubResourceAST,
)
from studio.verification.syntax_validators import (
    BaseSyntaxValidator,
    CSharpSyntaxValidator,
    GDScriptSyntaxValidator,
    HTMLSyntaxValidator,
    JavaScriptSyntaxValidator,
    MultiEngineSyntaxValidator,
    PythonSyntaxValidator,
)
from studio.verification.tscn_validator import (
    GODOT4_NODE_CLASSES,
    GODOT4_RESOURCE_TYPES,
    TSCNValidator,
)


class TestAdversarialGDScriptSyntax(unittest.TestCase):
    """Adversarial stress testing of pure-Python GDScriptSyntaxValidator."""

    def setUp(self) -> None:
        self.validator = GDScriptSyntaxValidator()

    def test_comments_with_unbalanced_quotes(self) -> None:
        """Comments containing unmatched single or double quotes must not corrupt state."""
        code = '''extends Node2D

# Comment with unbalanced " double quote
var a: int = 1
# Another comment with unmatched ' single quote
var b: String = "valid string"
# Comment with triple quotes """ and escaped \\" quotes
func _ready():
    pass
'''
        errors = self.validator.validate(code)
        self.assertEqual(len(errors), 0, f"Unexpected errors on valid code with quotes in comments: {errors}")

    def test_empirical_vulnerability_multiline_string_keywords(self) -> None:
        """
        Empirical finding: Multiline string containing code keywords (extends, func).
        The line-by-line declaration checker does not track multiline string state,
        falsely reporting 'Multiple extends declarations' or 'Unexpected indentation'.
        """
        code = '''extends Node

var embedded = """
extends CanvasItem
func fake():
    pass
"""

func real():
    pass
'''
        errors = self.validator.validate(code)
        # Verify that error is cleanly captured as DiagnosticError and document vulnerability
        self.assertTrue(any("Multiple 'extends' declarations" in e.message for e in errors))

    def test_empirical_vulnerability_escaped_quote_before_hash(self) -> None:
        """
        Empirical finding: `_strip_comment` treats escaped quote `\"` as closing the string,
        causing subsequent `#` in strings to be treated as comments, stripping the header colon.
        """
        code = r'''extends Node

func foo(s = "\"#"):
    pass
'''
        errors = self.validator.validate(code)
        # Documents false-positive missing colon diagnostic due to _strip_comment regex flaw
        self.assertTrue(any("missing trailing ':'" in e.message for e in errors))

    def test_empirical_vulnerability_annotation_nested_parens(self) -> None:
        """
        Empirical finding: Annotation parser uses stripped.find(')'), which matches
        the first ')' inside string arguments rather than the outer matching parenthesis.
        """
        code = '''extends Node

@export_enum("Fast (1)", "Slow (2)") var speed_preset: int = 0
'''
        errors = self.validator.validate(code)
        self.assertTrue(any("must precede 'var' or 'func'" in e.message for e in errors))

    def test_hash_inside_strings_without_escapes_handled(self) -> None:
        """String literals containing '#' comments without escaped quotes are correctly preserved."""
        code = '''extends Node

func get_color_hex(s = "#ff0000"):
    return s
'''
        errors = self.validator.validate(code)
        self.assertEqual(len(errors), 0, f"String with '#' falsely treated as comment: {errors}")

    def test_deeply_nested_indentation_blocks(self) -> None:
        """Stress test: 50 levels of nested indentation should not cause stack overflow."""
        lines = ["extends Node", "func deeply_nested():"]
        for i in range(1, 51):
            indent = "    " * i
            lines.append(f"{indent}if true:")
        lines.append("    " * 51 + "pass")
        code = "\n".join(lines)
        errors = self.validator.validate(code)
        self.assertEqual(len(errors), 0, f"Deeply nested blocks produced errors: {errors}")

    def test_tabs_and_spaces_on_empty_lines_ignored(self) -> None:
        """Empty lines containing random tabs or spaces should not trigger indentation mismatch."""
        code = "extends Node\n\n\t  \t\nfunc test():\n    pass\n"
        errors = self.validator.validate(code)
        self.assertEqual(len(errors), 0, f"Empty lines with whitespace produced errors: {errors}")

    def test_mixed_tabs_and_spaces_in_code_line_rejected(self) -> None:
        """Mixing tabs and spaces in leading code indentation must produce DiagnosticError."""
        code = "extends Node\nfunc test():\n \tpass\n"
        errors = self.validator.validate(code)
        self.assertTrue(any("Inconsistent indentation" in e.message for e in errors))

    def test_unclosed_multiline_string_at_eof(self) -> None:
        """Unclosed triple-quote string at EOF must be reported."""
        code = 'extends Node\nvar doc = """This is not closed'
        errors = self.validator.validate(code)
        self.assertTrue(any("Unterminated multiline string" in e.message for e in errors))

    def test_unclosed_delimiter_stack_reporting(self) -> None:
        """Multiple unclosed delimiters ({[ should all be diagnosed with positions."""
        code = "extends Node\nvar x = [({\n"
        errors = self.validator.validate(code)
        self.assertGreaterEqual(len(errors), 3)
        openers = {e.message for e in errors}
        self.assertTrue(any("Unclosed delimiter '['" in m for m in openers))
        self.assertTrue(any("Unclosed delimiter '('" in m for m in openers))
        self.assertTrue(any("Unclosed delimiter '{'" in m for m in openers))

    def test_multiple_extends_and_class_names(self) -> None:
        """Multiple extends or class_name statements must be rejected."""
        code = """extends Node
extends Sprite2D
class_name First
class_name Second
"""
        errors = self.validator.validate(code)
        self.assertTrue(any("Multiple 'extends' declarations" in e.message for e in errors))
        self.assertTrue(any("Multiple 'class_name' declarations" in e.message for e in errors))


class TestAdversarialMultiLanguageSyntax(unittest.TestCase):
    """Adversarial stress testing of Python, JS, HTML, and C# validators."""

    def test_python_syntax_ast_error_clean_diagnostics(self) -> None:
        """Adversarial malformed Python snippets must produce clean DiagnosticError."""
        v = PythonSyntaxValidator()
        malformed_snippets = [
            ("def foo( missing_paren:", "expected ':'"),
            ("x = [1, 2, 3", "was never closed"),
            ("    x = 10", "unexpected indent"),
            ("if True\n    pass", "expected ':'"),
        ]
        for snippet, _ in malformed_snippets:
            errors = v.validate(snippet)
            self.assertGreaterEqual(len(errors), 1)
            for err in errors:
                self.assertEqual(err.stage, "syntax_check")
                self.assertEqual(err.severity, "ERROR")
                self.assertIsNotNone(err.line)

    def test_javascript_pure_python_fallback_delimiters(self) -> None:
        """JavaScript fallback validator checks delimiter balance and multiline comments."""
        v = JavaScriptSyntaxValidator(force_fallback=True)
        # Unterminated block comment
        code = "/* Unterminated block comment at EOF\nfunction test() { return 1; }"
        errors = v.validate(code)
        self.assertTrue(any("Unterminated multiline comment" in e.message for e in errors))

        # Mismatched bracket
        code_mismatch = "function test() { return [1, 2}; }"
        errors_mismatch = v.validate(code_mismatch)
        self.assertTrue(any("Mismatched closing delimiter" in e.message for e in errors_mismatch))

    def test_empirical_vulnerability_javascript_fallback_unclosed_template_literal(self) -> None:
        """
        Empirical finding: JavaScript pure-Python fallback scanner skips reporting unclosed
        backticks in single-line checks and does not check for unclosed backticks at EOF.
        """
        v = JavaScriptSyntaxValidator(force_fallback=True)
        code = "const unclosed = `this template literal is never closed at end of file;"
        errors = v.validate(code)
        # Documents that pure-Python fallback misses unclosed template literal at EOF
        self.assertEqual(len(errors), 0)

    def test_html_tag_balancing_void_tags_and_inline_scripts(self) -> None:
        """HTML validator allows HTML5 void tags but catches unclosed container tags and script errors."""
        v = HTMLSyntaxValidator()
        # Valid void tags
        valid_html = "<div><img src='test.png'><br><input type='text'><hr></div>"
        self.assertEqual(len(v.validate(valid_html)), 0)

        # Unclosed tag at EOF
        unclosed = "<div><section><p>Text</p></section>"
        errors = v.validate(unclosed)
        self.assertTrue(any("Unclosed HTML tag <div>" in e.message for e in errors))

        # Mismatched closing tag
        mismatched = "<div><span>Text</div></span>"
        errors = v.validate(mismatched)
        self.assertTrue(any("Mismatched closing tag </div>" in e.message for e in errors))

    def test_csharp_preprocessor_directive_stack_checks(self) -> None:
        """C# validator balances #if / #endif and #region / #endregion."""
        v = CSharpSyntaxValidator()
        # Mismatched directive nesting
        code = """#if UNITY_EDITOR
#region InternalHelpers
#endif
#endregion
"""
        errors = v.validate(code)
        self.assertTrue(any("Mismatched '#endif'" in e.message for e in errors))

        # Unclosed #if directive
        code_unclosed = """#if UNITY_STANDALONE
public class GameController {}
"""
        errors_unclosed = v.validate(code_unclosed)
        self.assertTrue(any("Unclosed preprocessor directive '#if'" in e.message for e in errors_unclosed))

    def test_empirical_vulnerability_csharp_unclosed_verbatim_string(self) -> None:
        """
        Empirical finding: C# validator does not track multiline verbatim strings (@"...")
        across lines and misses unclosed verbatim strings at EOF when not enclosed in braces.
        """
        v = CSharpSyntaxValidator()
        code = 'string s = @"unclosed verbatim string;'
        errors = v.validate(code)
        self.assertEqual(len(errors), 0)

    def test_multi_engine_facade_routing_and_unsupported(self) -> None:
        """MultiEngineSyntaxValidator routes by language and handles unknown languages gracefully."""
        facade = MultiEngineSyntaxValidator()
        # Unknown language returns WARNING diagnostic
        errors = facade.validate("some code", language="brainfuck")
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].severity, "WARNING")
        self.assertIn("Unsupported language", errors[0].message)


class TestAdversarialTSCNValidator(unittest.TestCase):
    """Adversarial stress testing of Godot 4 .tscn schema and SceneAST validation."""

    def setUp(self) -> None:
        self.validator = TSCNValidator()

    def test_empty_tscn_file_rejected(self) -> None:
        """Empty scene text returns ERROR diagnostic."""
        errors = self.validator.validate_tscn_text("   \n\t  \n")
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].severity, "ERROR")
        self.assertIn("Scene file is empty", errors[0].message)

    def test_missing_or_invalid_gd_scene_header(self) -> None:
        """Scene missing [gd_scene] header tag is rejected."""
        tscn = "[node name=\"Root\" type=\"Node2D\"]\n"
        errors = self.validator.validate_tscn_text(tscn)
        self.assertTrue(any("Missing '[gd_scene ...]' scene header tag" in e.message for e in errors))

    def test_unsupported_format_versions(self) -> None:
        """Scene declaring format=2, format=4, or format=-1 must be diagnosed."""
        for fmt in (2, 4, -1):
            tscn = f"[gd_scene format={fmt}]\n[node name=\"Root\" type=\"Node2D\"]\n"
            errors = self.validator.validate_tscn_text(tscn)
            self.assertTrue(
                any(f"Unsupported scene format '{fmt}'" in e.message or "format_version" in e.message or "Missing 'format' attribute" in e.message for e in errors),
                f"Format {fmt} was not rejected: {errors}"
            )

    def test_cyclic_node_parenting_detection(self) -> None:
        """Cyclic parent relationships between nodes must be detected."""
        tscn = """[gd_scene format=3]

[node name="Root" type="Node2D"]

[node name="NodeA" type="Node2D" parent="NodeB"]

[node name="NodeB" type="Node2D" parent="NodeA"]
"""
        errors = self.validator.validate_tscn_text(tscn)
        self.assertTrue(any("Cyclic node parenting detected" in e.message for e in errors))

    def test_self_parenting_node_detection(self) -> None:
        """Node having parent equal to its own name is flagged."""
        tscn = """[gd_scene format=3]

[node name="Root" type="Node2D"]

[node name="RecursiveNode" type="Node2D" parent="RecursiveNode"]
"""
        errors = self.validator.validate_tscn_text(tscn)
        self.assertTrue(any("self-parenting" in e.message for e in errors))

    def test_missing_parent_reference(self) -> None:
        """Node referencing non-existent parent node is flagged."""
        tscn = """[gd_scene format=3]

[node name="Root" type="Node2D"]

[node name="Child" type="Node2D" parent="GhostParent"]
"""
        errors = self.validator.validate_tscn_text(tscn)
        self.assertTrue(any("references non-existent parent 'GhostParent'" in e.message for e in errors))

    def test_topological_parent_before_child_ordering(self) -> None:
        """Child declared before its parent in file order is flagged."""
        tscn = """[gd_scene format=3]

[node name="Root" type="Node2D"]

[node name="GrandChild" type="Node2D" parent="ParentNode"]

[node name="ParentNode" type="Node2D" parent="."]
"""
        errors = self.validator.validate_tscn_text(tscn)
        self.assertTrue(any("references non-existent parent 'ParentNode'" in e.message for e in errors))

    def test_duplicate_sibling_names_under_same_parent(self) -> None:
        """Two child nodes with identical names under the same parent must be rejected."""
        tscn = """[gd_scene format=3]

[node name="Root" type="Node2D"]

[node name="Bullet" type="Node2D" parent="."]

[node name="Bullet" type="Node2D" parent="."]
"""
        errors = self.validator.validate_tscn_text(tscn)
        self.assertTrue(any("Duplicate sibling node name 'Bullet'" in e.message for e in errors))

    def test_duplicate_external_and_sub_resource_ids(self) -> None:
        """Duplicate IDs across external resources or sub-resources are rejected."""
        tscn = """[gd_scene load_steps=5 format=3]

[ext_resource type="Texture2D" path="res://icon1.png" id="1_tex"]
[ext_resource type="Texture2D" path="res://icon2.png" id="1_tex"]

[sub_resource type="RectangleShape2D" id="shape_1"]
[sub_resource type="RectangleShape2D" id="shape_1"]

[node name="Root" type="Sprite2D"]
"""
        errors = self.validator.validate_tscn_text(tscn)
        self.assertTrue(any("Duplicate external resource id '1_tex'" in e.message for e in errors))
        self.assertTrue(any("Duplicate sub-resource id 'shape_1'" in e.message for e in errors))

    def test_resource_path_traversal_and_res_prefix_enforcement(self) -> None:
        """Resource paths must start with 'res://' and reject '..' traversal."""
        tscn = """[gd_scene load_steps=3 format=3]

[ext_resource type="Texture2D" path="/absolute/path/icon.png" id="1_bad"]
[ext_resource type="Texture2D" path="res://assets/../../secret.png" id="2_trav"]

[node name="Root" type="Sprite2D"]
"""
        errors = self.validator.validate_tscn_text(tscn)
        self.assertTrue(any("does not start with 'res://'" in e.message for e in errors))
        self.assertTrue(any("Path traversal detected" in e.message for e in errors))

    def test_script_attachment_validation(self) -> None:
        """Attached script must reference valid Script ext_resource."""
        tscn = """[gd_scene load_steps=3 format=3]

[ext_resource type="Texture2D" path="res://icon.png" id="1_tex"]

[node name="Root" type="Node2D"]
script = ExtResource("1_tex")

[node name="Child" type="Node2D" parent="."]
script = ExtResource("99_missing")
"""
        errors = self.validator.validate_tscn_text(tscn)
        self.assertTrue(any("references resource of type 'Texture2D', expected 'Script'" in e.message for e in errors))
        self.assertTrue(any("references unknown script_id '99_missing'" in e.message for e in errors))

    def test_signal_connections_validations_ast_and_text(self) -> None:
        """Signal wiring requires valid emitter, receiver, non-empty signal, and non-empty method."""
        # 1. Text roundtrip with unknown emitter node
        tscn = """[gd_scene format=3]

[node name="Root" type="Node2D"]

[connection signal="pressed" from="Ghost" to="Root" method="_on_pressed"]
"""
        errors = self.validator.validate_tscn_text(tscn)
        self.assertTrue(any("from' node 'Ghost' does not exist" in e.message for e in errors))

        # 2. In-memory SceneAST with empty signal and method
        scene = SceneAST(
            name="Main",
            path="scenes/main.tscn",
            nodes=[NodeAST(name="Root", type="Node2D")],
            connections=[
                ConnectionAST(signal="", from_node="Root", to_node="Root", method=""),
            ],
        )
        ast_errors = self.validator.validate_scene_ast(scene)
        self.assertTrue(any("empty signal name" in e.message for e in ast_errors))
        self.assertTrue(any("empty callback method name" in e.message for e in ast_errors))

    def test_load_steps_declared_mismatch(self) -> None:
        """Declared load_steps mismatching actual resources generates warning."""
        tscn = """[gd_scene load_steps=10 format=3]

[ext_resource type="Texture2D" path="res://icon.png" id="1_tex"]

[node name="Root" type="Sprite2D"]
"""
        errors = self.validator.validate_tscn_text(tscn)
        self.assertTrue(any("load_steps mismatch" in e.message for e in errors))

    def test_empirical_vulnerability_missing_load_steps_with_ext_resources(self) -> None:
        """
        Empirical finding: When load_steps is omitted completely from header,
        parsed_load_steps is None, so the validator does not enforce load_steps = N_ext + N_sub + 1.
        """
        tscn = """[gd_scene format=3]

[ext_resource type="Texture2D" path="res://icon.png" id="1_tex"]

[node name="Root" type="Sprite2D"]
"""
        errors = self.validator.validate_tscn_text(tscn)
        # Documents that missing load_steps produces 0 warnings because parsed_load_steps is None
        self.assertEqual(len(errors), 0)


class TestAdversarialCrashResilience(unittest.TestCase):
    """Stress tests ensuring validators never crash with unhandled exceptions on extreme inputs."""

    def test_syntax_validators_fuzz_payloads_never_crash(self) -> None:
        """Inject hostile fuzzing payloads into all syntax validators."""
        payloads = [
            "\x00" * 50,                                    # Null bytes
            '"""' * 20,                                     # Repeated quotes
            "({[<" * 200,                                   # Huge unclosed delimiters
            "# " * 500,                                     # Huge comment chain
            " " * 1000 + "var x = 1",                       # Huge indentation
            "var " + "a" * 50000 + " = 1",                  # 50k identifier
            "/*" * 100 + "*/" * 50,                         # Interleaved comments
            "<" * 500 + ">" * 250,                          # Malformed HTML
            "@" + "\n@" * 50,                               # Bare annotations
        ]

        validators: List[BaseSyntaxValidator] = [
            GDScriptSyntaxValidator(),
            PythonSyntaxValidator(),
            JavaScriptSyntaxValidator(force_fallback=True),
            HTMLSyntaxValidator(),
            CSharpSyntaxValidator(),
        ]

        for val in validators:
            for payload in payloads:
                try:
                    errors = val.validate(payload, file_path="adversarial_test")
                    self.assertIsInstance(errors, list)
                    for err in errors:
                        self.assertIsInstance(err, DiagnosticError)
                except Exception as ex:
                    self.fail(f"Validator {val.language_name} crashed with unhandled exception on payload: {ex}")

    def test_tscn_validator_fuzz_payloads_never_crash(self) -> None:
        """Inject hostile fuzzing payloads into TSCNValidator."""
        tscn_validator = TSCNValidator()
        payloads = [
            "\x00" * 100,
            "[gd_scene" + " " * 500,
            "[gd_scene format=-99999999999999999999999999999999999999999]",
            "[node name=\"" + "n" * 10000 + "\"]",
            "[[[[[[[",
            "[connection " + "a" * 1000 + "]",
            ";;;;\n\n\n   \n",
        ]
        for payload in payloads:
            try:
                errors = tscn_validator.validate_tscn_text(payload, file_path="fuzz.tscn")
                self.assertIsInstance(errors, list)
                for err in errors:
                    self.assertIsInstance(err, DiagnosticError)
            except Exception as ex:
                self.fail(f"TSCNValidator crashed with unhandled exception on payload: {ex}")


if __name__ == "__main__":
    unittest.main()
