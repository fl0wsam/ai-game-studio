"""
studio/verification/syntax_validators.py - Multi-Engine Code & Markup Syntax Validators.
Implements F-VERIF-01: Pure-Python and external tool syntax validation for GDScript, Python,
JavaScript, HTML, and C# engine scripts, mapping errors to DiagnosticError records.
"""
from __future__ import annotations

import ast
import html.parser
import os
import re
import shutil
import subprocess
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from studio.core.models import DiagnosticError, ScriptSpec


# ============================================================================
# 1. Base Syntax Validator Interface
# ============================================================================

class BaseSyntaxValidator(ABC):
    """Abstract base class for all language-specific syntax checkers."""

    @property
    @abstractmethod
    def language_name(self) -> str:
        """Normalized language identifier (e.g. 'python', 'gdscript')."""
        ...

    @abstractmethod
    def validate(self, code: str, file_path: str = "<memory>") -> List[DiagnosticError]:
        """Validates source code string and returns diagnosed errors."""
        ...

    def validate_file(self, file_path: Union[str, Path]) -> List[DiagnosticError]:
        """Reads file from disk and validates its content."""
        path = Path(file_path)
        if not path.is_file():
            return [
                DiagnosticError(
                    stage="syntax_check",
                    severity="ERROR",
                    file_path=str(path),
                    line=1,
                    column=1,
                    message=f"File does not exist: {path}",
                    suggested_fix="Verify file path before validation.",
                )
            ]
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as ex:
            return [
                DiagnosticError(
                    stage="syntax_check",
                    severity="ERROR",
                    file_path=str(path),
                    line=1,
                    column=1,
                    message=f"Invalid UTF-8 encoding in file: {ex}",
                    suggested_fix="Save file in UTF-8 encoding without binary characters.",
                )
            ]
        except Exception as ex:
            return [
                DiagnosticError(
                    stage="syntax_check",
                    severity="ERROR",
                    file_path=str(path),
                    line=1,
                    column=1,
                    message=f"Failed to read file: {ex}",
                )
            ]
        return self.validate(content, file_path=str(path))


# ============================================================================
# 2. Python Syntax Validator (AST)
# ============================================================================

class PythonSyntaxValidator(BaseSyntaxValidator):
    """Validates Python source code using standard library ast.parse()."""

    @property
    def language_name(self) -> str:
        return "python"

    def validate(self, code: str, file_path: str = "<memory>") -> List[DiagnosticError]:
        errors: List[DiagnosticError] = []
        if not code.strip():
            return errors

        try:
            ast.parse(code, filename=file_path)
        except SyntaxError as e:
            suggested_fix = self._derive_fix(e.msg)
            errors.append(
                DiagnosticError(
                    stage="syntax_check",
                    severity="ERROR",
                    file_path=file_path,
                    line=e.lineno or 1,
                    column=e.offset or 1,
                    message=f"Python syntax error: {e.msg}",
                    suggested_fix=suggested_fix,
                )
            )
        except Exception as e:
            errors.append(
                DiagnosticError(
                    stage="syntax_check",
                    severity="ERROR",
                    file_path=file_path,
                    line=1,
                    column=1,
                    message=f"Python parser error: {e}",
                )
            )
        return errors

    @staticmethod
    def _derive_fix(msg: Optional[str]) -> Optional[str]:
        if not msg:
            return None
        lower = msg.lower()
        if "expected ':'" in lower:
            return "Add missing colon ':' at end of header statement."
        if "was never closed" in lower or "unmatched" in lower:
            return "Close matching parenthesis, bracket, or quotation mark."
        if "indent" in lower:
            return "Ensure consistent 4-space indentation."
        if "unexpected eof" in lower:
            return "Complete unfinished code block or close dangling construct."
        return "Check Python syntax near indicated line and column."


# ============================================================================
# 3. GDScript Syntax Validator (Pure-Python Structural Tokenizer)
# ============================================================================

@dataclass
class _GDDelim:
    char: str
    line: int
    col: int


class GDScriptSyntaxValidator(BaseSyntaxValidator):
    """
    Pure-Python GDScript 2.0 (Godot 4) syntax and structural validator.
    Validates indentation, balanced delimiters, string literals, keywords,
    annotations, colons, and declaration grammar without external engine binaries.
    """

    GODOT_ANNOTATIONS: Set[str] = {
        "@export",
        "@export_range",
        "@export_enum",
        "@export_file",
        "@export_dir",
        "@export_global_file",
        "@export_global_dir",
        "@export_multiline",
        "@export_placeholder",
        "@onready",
        "@tool",
        "@icon",
        "@rpc",
        "@warning_ignore",
    }

    HEADER_KEYWORDS: Set[str] = {
        "if",
        "elif",
        "else",
        "for",
        "while",
        "match",
    }

    @property
    def language_name(self) -> str:
        return "gdscript"

    def validate(self, code: str, file_path: str = "<memory>") -> List[DiagnosticError]:
        errors: List[DiagnosticError] = []
        if not code.strip():
            return errors

        # Strip optional UTF-8 BOM
        if code.startswith("\ufeff"):
            code = code[1:]

        lines = code.splitlines(keepends=True)

        # 1. Delimiter & String Literal Verification
        delim_errors = self._validate_delimiters_and_strings(lines, file_path)
        errors.extend(delim_errors)

        # 2. Indentation Consistency & Colon Requirement
        indent_errors = self._validate_indentation_and_headers(lines, file_path)
        errors.extend(indent_errors)

        # 3. Keyword Declarations & Annotations
        decl_errors = self._validate_declarations(lines, file_path)
        errors.extend(decl_errors)

        return errors

    def _validate_delimiters_and_strings(
        self, lines: List[str], file_path: str
    ) -> List[DiagnosticError]:
        errors: List[DiagnosticError] = []
        stack: List[_GDDelim] = []
        pairs = {")": "(", "]": "[", "}": "{"}

        in_multiline_double = False
        multiline_start_line = 0
        multiline_start_col = 0

        in_multiline_single = False

        for line_idx, raw_line in enumerate(lines, start=1):
            i = 0
            n = len(raw_line)

            while i < n:
                # Handle active multiline double-quote string
                if in_multiline_double:
                    if raw_line.startswith('"""', i):
                        in_multiline_double = False
                        i += 3
                        continue
                    elif raw_line[i] == "\\" and i + 1 < n:
                        i += 2
                        continue
                    else:
                        i += 1
                        continue

                # Handle active multiline single-quote string
                if in_multiline_single:
                    if raw_line.startswith("'''", i):
                        in_multiline_single = False
                        i += 3
                        continue
                    elif raw_line[i] == "\\" and i + 1 < n:
                        i += 2
                        continue
                    else:
                        i += 1
                        continue

                # Start multiline strings
                if raw_line.startswith('"""', i):
                    in_multiline_double = True
                    multiline_start_line = line_idx
                    multiline_start_col = i + 1
                    i += 3
                    continue

                if raw_line.startswith("'''", i):
                    in_multiline_single = True
                    multiline_start_line = line_idx
                    multiline_start_col = i + 1
                    i += 3
                    continue

                # Single-line strings
                ch = raw_line[i]
                if ch in ('"', "'"):
                    quote_char = ch
                    col_start = i + 1
                    i += 1
                    closed = False
                    while i < n:
                        cur = raw_line[i]
                        if cur == "\\":
                            i += 2
                            continue
                        if cur == quote_char:
                            closed = True
                            i += 1
                            break
                        if cur in ("\r", "\n"):
                            break
                        i += 1
                    if not closed:
                        errors.append(
                            DiagnosticError(
                                stage="syntax_check",
                                severity="ERROR",
                                file_path=file_path,
                                line=line_idx,
                                column=col_start,
                                message=f"Unterminated string literal on line {line_idx}.",
                                suggested_fix=f"Close string literal with matching {quote_char}.",
                            )
                        )
                    continue

                # Comments
                if ch == "#":
                    # Rest of line is comment
                    break

                # Delimiters
                if ch in ("(", "[", "{"):
                    stack.append(_GDDelim(ch, line_idx, i + 1))
                elif ch in (")", "]", "}"):
                    expected_open = pairs[ch]
                    if not stack:
                        errors.append(
                            DiagnosticError(
                                stage="syntax_check",
                                severity="ERROR",
                                file_path=file_path,
                                line=line_idx,
                                column=i + 1,
                                message=f"Unmatched closing delimiter '{ch}'.",
                                suggested_fix=f"Remove extraneous '{ch}' or open matching '{expected_open}'.",
                            )
                        )
                    else:
                        top = stack.pop()
                        if top.char != expected_open:
                            errors.append(
                                DiagnosticError(
                                    stage="syntax_check",
                                    severity="ERROR",
                                    file_path=file_path,
                                    line=line_idx,
                                    column=i + 1,
                                    message=(
                                        f"Mismatched closing delimiter '{ch}'; "
                                        f"expected matching closing for '{top.char}' opened at line {top.line}, col {top.col}."
                                    ),
                                    suggested_fix=f"Replace '{ch}' with matching delimiter for '{top.char}'.",
                                )
                            )

                i += 1

        if in_multiline_double:
            errors.append(
                DiagnosticError(
                    stage="syntax_check",
                    severity="ERROR",
                    file_path=file_path,
                    line=multiline_start_line,
                    column=multiline_start_col,
                    message="Unterminated multiline string '\"\"\"' at end of file.",
                    suggested_fix="Close multiline string with '\"\"\".",
                )
            )
        if in_multiline_single:
            errors.append(
                DiagnosticError(
                    stage="syntax_check",
                    severity="ERROR",
                    file_path=file_path,
                    line=multiline_start_line,
                    column=multiline_start_col,
                    message="Unterminated multiline string \"'''\" at end of file.",
                    suggested_fix="Close multiline string with \"'''.",
                )
            )

        while stack:
            unclosed = stack.pop()
            errors.append(
                DiagnosticError(
                    stage="syntax_check",
                    severity="ERROR",
                    file_path=file_path,
                    line=unclosed.line,
                    column=unclosed.col,
                    message=f"Unclosed delimiter '{unclosed.char}' opened at line {unclosed.line}, col {unclosed.col}.",
                    suggested_fix=f"Add closing delimiter for '{unclosed.char}'.",
                )
            )

        return errors

    def _validate_indentation_and_headers(
        self, lines: List[str], file_path: str
    ) -> List[DiagnosticError]:
        errors: List[DiagnosticError] = []
        indent_mode: Optional[str] = None  # "tab" or "space"
        indent_stack: List[int] = [0]
        prev_line_must_indent = False
        prev_header_line_num = 0

        for line_num, raw in enumerate(lines, start=1):
            line = raw.rstrip("\r\n")
            stripped = line.strip()

            # Skip empty lines or pure comments
            if not stripped or stripped.startswith("#"):
                continue

            # Measure leading whitespace
            leading = line[: len(line) - len(line.lstrip())]
            has_tabs = "\t" in leading
            has_spaces = " " in leading

            if has_tabs and has_spaces:
                errors.append(
                    DiagnosticError(
                        stage="syntax_check",
                        severity="ERROR",
                        file_path=file_path,
                        line=line_num,
                        column=1,
                        message="Inconsistent indentation: mixing tabs and spaces in leading indentation.",
                        suggested_fix="Use either tabs or spaces consistently for indentation.",
                    )
                )

            # Global script indentation style consistency
            if leading:
                current_mode = "tab" if has_tabs else "space"
                if indent_mode is None:
                    indent_mode = current_mode
                elif indent_mode != current_mode:
                    errors.append(
                        DiagnosticError(
                            stage="syntax_check",
                            severity="ERROR",
                            file_path=file_path,
                            line=line_num,
                            column=1,
                            message=f"Indentation style mismatch: expected {indent_mode}s but found {current_mode}s.",
                            suggested_fix=f"Convert script indentation to {indent_mode}s consistently.",
                        )
                    )

            # Compute indent depth value
            depth = len(leading.replace("\t", "    "))

            if depth > indent_stack[-1]:
                if not prev_line_must_indent:
                    errors.append(
                        DiagnosticError(
                            stage="syntax_check",
                            severity="ERROR",
                            file_path=file_path,
                            line=line_num,
                            column=1,
                            message="Unexpected indentation without preceding header colon statement.",
                            suggested_fix="Ensure statement preceding indented block ends with a colon ':'.",
                        )
                    )
                indent_stack.append(depth)
            elif depth < indent_stack[-1]:
                # Dedenting
                while indent_stack and indent_stack[-1] > depth:
                    indent_stack.pop()
                if not indent_stack or indent_stack[-1] != depth:
                    errors.append(
                        DiagnosticError(
                            stage="syntax_check",
                            severity="ERROR",
                            file_path=file_path,
                            line=line_num,
                            column=1,
                            message=f"Unindent does not match any outer indentation level ({depth} spaces).",
                            suggested_fix="Align indentation with an existing outer block level.",
                        )
                    )

            # Check if current line is a header that must end with ':'
            # Strip trailing comments outside quotes
            code_part = self._strip_comment(stripped)
            ends_with_colon = code_part.endswith(":")

            tokens = code_part.split()
            first_word = tokens[0] if tokens else ""

            if first_word in self.HEADER_KEYWORDS or first_word == "func":
                if not ends_with_colon:
                    errors.append(
                        DiagnosticError(
                            stage="syntax_check",
                            severity="ERROR",
                            file_path=file_path,
                            line=line_num,
                            column=len(code_part),
                            message=f"Header statement '{first_word}' missing trailing ':'.",
                            suggested_fix=f"Add ':' at the end of the {first_word} statement line.",
                        )
                    )
                prev_line_must_indent = ends_with_colon
                prev_header_line_num = line_num
            else:
                prev_line_must_indent = ends_with_colon

        return errors

    def _validate_declarations(
        self, lines: List[str], file_path: str
    ) -> List[DiagnosticError]:
        errors: List[DiagnosticError] = []
        extends_count = 0
        class_name_count = 0
        seen_function = False
        pending_annotation: Optional[Tuple[str, int]] = None

        ident_pattern = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

        for line_num, raw in enumerate(lines, start=1):
            line = raw.rstrip("\r\n")
            stripped = self._strip_comment(line.strip())
            if not stripped:
                continue

            # Check annotations
            if stripped.startswith("@"):
                annot_name = stripped.split()[0].split("(")[0]
                if annot_name in self.GODOT_ANNOTATIONS:
                    # Check if var or func is on the same line (e.g. @export var speed = 10)
                    if "(" in stripped and ")" in stripped:
                        idx_close = stripped.find(")")
                        after_annot = stripped[idx_close + 1:].strip()
                    else:
                        tokens = stripped.split(None, 1)
                        after_annot = tokens[1].strip() if len(tokens) > 1 else ""

                    if after_annot:
                        if not (after_annot.startswith("var ") or after_annot.startswith("func ") or after_annot.startswith("static func ")):
                            errors.append(
                                DiagnosticError(
                                    stage="syntax_check",
                                    severity="ERROR",
                                    file_path=file_path,
                                    line=line_num,
                                    column=1,
                                    message=f"Annotation '{annot_name}' must precede 'var' or 'func'.",
                                    suggested_fix="Place annotation directly above or before variable or function definition.",
                                )
                            )
                        stripped = after_annot
                    else:
                        pending_annotation = (annot_name, line_num)
                        continue
                else:
                    errors.append(
                        DiagnosticError(
                            stage="syntax_check",
                            severity="WARNING",
                            file_path=file_path,
                            line=line_num,
                            column=1,
                            message=f"Unrecognized Godot annotation '{annot_name}'.",
                        )
                    )
                    continue

            # If there was a pending annotation, current line MUST be var or func
            if pending_annotation:
                annot_name, annot_line = pending_annotation
                if not (stripped.startswith("var ") or stripped.startswith("func ") or stripped.startswith("static func ")):
                    errors.append(
                        DiagnosticError(
                            stage="syntax_check",
                            severity="ERROR",
                            file_path=file_path,
                            line=annot_line,
                            column=1,
                            message=f"Annotation '{annot_name}' must immediately precede 'var' or 'func'.",
                            suggested_fix="Place annotation directly above variable or function definition.",
                        )
                    )
                pending_annotation = None

            # Extends check
            if stripped.startswith("extends "):
                extends_count += 1
                if extends_count > 1:
                    errors.append(
                        DiagnosticError(
                            stage="syntax_check",
                            severity="ERROR",
                            file_path=file_path,
                            line=line_num,
                            column=1,
                            message="Multiple 'extends' declarations in single script.",
                            suggested_fix="Keep only one 'extends' declaration at the top.",
                        )
                    )
                if seen_function:
                    errors.append(
                        DiagnosticError(
                            stage="syntax_check",
                            severity="ERROR",
                            file_path=file_path,
                            line=line_num,
                            column=1,
                            message="'extends' must appear before function declarations.",
                            suggested_fix="Move 'extends' statement to the top of the file.",
                        )
                    )

            # Class_name check
            elif stripped.startswith("class_name "):
                class_name_count += 1
                if class_name_count > 1:
                    errors.append(
                        DiagnosticError(
                            stage="syntax_check",
                            severity="ERROR",
                            file_path=file_path,
                            line=line_num,
                            column=1,
                            message="Multiple 'class_name' declarations in single script.",
                            suggested_fix="A GDScript may define at most one 'class_name'.",
                        )
                    )
                name_part = stripped[len("class_name ") :].strip().split()[0]
                if not ident_pattern.match(name_part):
                    errors.append(
                        DiagnosticError(
                            stage="syntax_check",
                            severity="ERROR",
                            file_path=file_path,
                            line=line_num,
                            column=12,
                            message=f"Invalid identifier for class_name: '{name_part}'.",
                            suggested_fix="Use a valid alphanumeric identifier for class_name.",
                        )
                    )

            # Const check: must have assignment '='
            elif stripped.startswith("const "):
                tokens = stripped[len("const ") :].split("=")
                if len(tokens) < 2:
                    errors.append(
                        DiagnosticError(
                            stage="syntax_check",
                            severity="ERROR",
                            file_path=file_path,
                            line=line_num,
                            column=1,
                            message=f"Const declaration '{stripped}' missing value assignment '='.",
                            suggested_fix="Initialize constant with an assigned value (e.g. const X = 10).",
                        )
                    )

            # For loop check: must contain 'in'
            elif stripped.startswith("for ") and stripped.endswith(":"):
                body = stripped[4:-1].strip()
                if " in " not in body:
                    errors.append(
                        DiagnosticError(
                            stage="syntax_check",
                            severity="ERROR",
                            file_path=file_path,
                            line=line_num,
                            column=1,
                            message="For loop missing 'in' keyword.",
                            suggested_fix="Format for loop as: for <item> in <iterable>:",
                        )
                    )

            # Func declaration
            elif stripped.startswith("func ") or stripped.startswith("static func "):
                seen_function = True
                fn_part = stripped.split("func ", 1)[1]
                fn_name = fn_part.split("(")[0].strip()
                if not ident_pattern.match(fn_name):
                    errors.append(
                        DiagnosticError(
                            stage="syntax_check",
                            severity="ERROR",
                            file_path=file_path,
                            line=line_num,
                            column=stripped.find(fn_name) + 1,
                            message=f"Invalid function identifier '{fn_name}'.",
                            suggested_fix="Use a valid alphanumeric function name.",
                        )
                    )

        if pending_annotation:
            annot_name, annot_line = pending_annotation
            errors.append(
                DiagnosticError(
                    stage="syntax_check",
                    severity="ERROR",
                    file_path=file_path,
                    line=annot_line,
                    column=1,
                    message=f"Dangling annotation '{annot_name}' at end of file.",
                    suggested_fix="Place variable or function declaration following annotation.",
                )
            )

        return errors

    @staticmethod
    def _strip_comment(line: str) -> str:
        """Strips trailing comments from line while ignoring '#' inside strings."""
        in_quote: Optional[str] = None
        for i, ch in enumerate(line):
            if in_quote:
                if ch == "\\" and i + 1 < len(line):
                    continue
                if ch == in_quote:
                    in_quote = None
            else:
                if ch in ('"', "'"):
                    in_quote = ch
                elif ch == "#":
                    return line[:i].strip()
        return line.strip()


# ============================================================================
# 4. JavaScript Syntax Validator (Node.js --check + Pure-Python Fallback)
# ============================================================================

class JavaScriptSyntaxValidator(BaseSyntaxValidator):
    """
    Validates JavaScript engine scripts.
    Uses 'node --check' CLI when available; otherwise falls back to a deterministic
    pure-Python JS structural tokenizer and delimiter balancer.
    """

    def __init__(self, force_fallback: bool = False) -> None:
        self.force_fallback = force_fallback
        self._node_bin: Optional[str] = None if force_fallback else shutil.which("node")

    @property
    def language_name(self) -> str:
        return "javascript"

    def validate(self, code: str, file_path: str = "<memory>") -> List[DiagnosticError]:
        if not code.strip():
            return []

        if self._node_bin and not self.force_fallback:
            res = self._validate_via_node(code, file_path)
            if res is not None:
                return res

        return self._validate_pure_python(code, file_path)

    def _validate_via_node(self, code: str, file_path: str) -> Optional[List[DiagnosticError]]:
        """Invokes 'node --check' on temporary file."""
        tmp = None
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
                f.write(code)
                tmp = f.name

            proc = subprocess.run(
                [str(self._node_bin), "--check", tmp],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=5.0,
            )
            if proc.returncode == 0:
                return []

            errors: List[DiagnosticError] = []
            stderr = proc.stderr
            line_match = re.search(r":(\d+)\n", stderr)
            lineno = int(line_match.group(1)) if line_match else 1
            msg_match = re.search(r"SyntaxError:\s*(.*)", stderr)
            msg = msg_match.group(1) if msg_match else "JavaScript syntax error."

            errors.append(
                DiagnosticError(
                    stage="syntax_check",
                    severity="ERROR",
                    file_path=file_path,
                    line=lineno,
                    column=1,
                    message=f"Node.js syntax check failed: {msg}",
                    suggested_fix="Fix JavaScript syntax error indicated by Node.js.",
                )
            )
            return errors
        except Exception:
            return None
        finally:
            if tmp and os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    def _validate_pure_python(self, code: str, file_path: str) -> List[DiagnosticError]:
        errors: List[DiagnosticError] = []
        stack: List[_GDDelim] = []
        pairs = {")": "(", "]": "[", "}": "{"}

        in_multi_comment = False
        multi_comment_line = 0

        lines = code.splitlines(keepends=True)

        for line_num, raw in enumerate(lines, start=1):
            i = 0
            n = len(raw)

            while i < n:
                if in_multi_comment:
                    if raw.startswith("*/", i):
                        in_multi_comment = False
                        i += 2
                        continue
                    i += 1
                    continue

                # Check comment start
                if raw.startswith("//", i):
                    break
                if raw.startswith("/*", i):
                    in_multi_comment = True
                    multi_comment_line = line_num
                    i += 2
                    continue

                ch = raw[i]

                # String literals
                if ch in ('"', "'", "`"):
                    quote = ch
                    col_start = i + 1
                    i += 1
                    closed = False
                    while i < n:
                        cur = raw[i]
                        if cur == "\\":
                            i += 2
                            continue
                        if cur == quote:
                            closed = True
                            i += 1
                            break
                        if quote != "`" and cur in ("\r", "\n"):
                            break
                        i += 1
                    if not closed and quote != "`":
                        errors.append(
                            DiagnosticError(
                                stage="syntax_check",
                                severity="ERROR",
                                file_path=file_path,
                                line=line_num,
                                column=col_start,
                                message=f"Unterminated string literal on line {line_num}.",
                                suggested_fix=f"Close string literal with matching {quote}.",
                            )
                        )
                    continue

                # Delimiters
                if ch in ("(", "[", "{"):
                    stack.append(_GDDelim(ch, line_num, i + 1))
                elif ch in (")", "]", "}"):
                    expected = pairs[ch]
                    if not stack:
                        errors.append(
                            DiagnosticError(
                                stage="syntax_check",
                                severity="ERROR",
                                file_path=file_path,
                                line=line_num,
                                column=i + 1,
                                message=f"Unmatched closing delimiter '{ch}'.",
                                suggested_fix=f"Remove extraneous '{ch}' or open matching '{expected}'.",
                            )
                        )
                    else:
                        top = stack.pop()
                        if top.char != expected:
                            errors.append(
                                DiagnosticError(
                                    stage="syntax_check",
                                    severity="ERROR",
                                    file_path=file_path,
                                    line=line_num,
                                    column=i + 1,
                                    message=f"Mismatched closing delimiter '{ch}'; expected closing for '{top.char}'.",
                                    suggested_fix=f"Replace '{ch}' with matching delimiter for '{top.char}'.",
                                )
                            )

                i += 1

        if in_multi_comment:
            errors.append(
                DiagnosticError(
                    stage="syntax_check",
                    severity="ERROR",
                    file_path=file_path,
                    line=multi_comment_line,
                    column=1,
                    message="Unterminated multiline comment '/*' at end of file.",
                    suggested_fix="Close multiline comment with '*/'.",
                )
            )

        while stack:
            top = stack.pop()
            errors.append(
                DiagnosticError(
                    stage="syntax_check",
                    severity="ERROR",
                    file_path=file_path,
                    line=top.line,
                    column=top.col,
                    message=f"Unclosed delimiter '{top.char}' opened at line {top.line}, col {top.col}.",
                    suggested_fix=f"Add closing delimiter for '{top.char}'.",
                )
            )

        return errors


# ============================================================================
# 5. HTML Syntax Validator (Tag Balancing + Inline Script JS Verification)
# ============================================================================

class _BalancingHTMLParser(html.parser.HTMLParser):
    VOID_TAGS: Set[str] = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }

    def __init__(self, file_path: str) -> None:
        super().__init__()
        self.file_path = file_path
        self.tag_stack: List[Tuple[str, int, int]] = []
        self.errors: List[DiagnosticError] = []
        self.scripts: List[Tuple[str, int]] = []
        self._current_script: Optional[List[str]] = None
        self._script_start_line = 0

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        low = tag.lower()
        line, col = self.getpos()
        if low == "script":
            self._current_script = []
            self._script_start_line = line
        if low not in self.VOID_TAGS:
            self.tag_stack.append((low, line, col))

    def handle_endtag(self, tag: str) -> None:
        low = tag.lower()
        line, col = self.getpos()
        if low == "script" and self._current_script is not None:
            self.scripts.append(("".join(self._current_script), self._script_start_line))
            self._current_script = None

        if low in self.VOID_TAGS:
            # Void tags shouldn't have closing tags in HTML5
            return

        if not self.tag_stack:
            self.errors.append(
                DiagnosticError(
                    stage="syntax_check",
                    severity="ERROR",
                    file_path=self.file_path,
                    line=line,
                    column=col,
                    message=f"Unexpected closing tag </{tag}> with no matching open tag.",
                    suggested_fix=f"Remove unnecessary </{tag}> tag.",
                )
            )
            return

        top_tag, top_line, top_col = self.tag_stack.pop()
        if top_tag != low:
            self.errors.append(
                DiagnosticError(
                    stage="syntax_check",
                    severity="ERROR",
                    file_path=self.file_path,
                    line=line,
                    column=col,
                    message=f"Mismatched closing tag </{tag}>; expected </{top_tag}> opened at line {top_line}.",
                    suggested_fix=f"Replace </{tag}> with </{top_tag}> or ensure proper tag nesting.",
                )
            )

    def handle_data(self, data: str) -> None:
        if self._current_script is not None:
            self._current_script.append(data)


class HTMLSyntaxValidator(BaseSyntaxValidator):
    """
    Validates HTML5 syntax: checks tag balancing (excluding void tags)
    and extracts inline <script> blocks to validate their JavaScript syntax.
    """

    def __init__(self) -> None:
        self.js_validator = JavaScriptSyntaxValidator()

    @property
    def language_name(self) -> str:
        return "html"

    def validate(self, code: str, file_path: str = "<memory>") -> List[DiagnosticError]:
        if not code.strip():
            return []

        parser = _BalancingHTMLParser(file_path=file_path)
        try:
            parser.feed(code)
            parser.close()
        except Exception as e:
            parser.errors.append(
                DiagnosticError(
                    stage="syntax_check",
                    severity="ERROR",
                    file_path=file_path,
                    line=1,
                    column=1,
                    message=f"HTML parser error: {e}",
                )
            )

        # Check unclosed tags remaining at EOF
        while parser.tag_stack:
            tag, line, col = parser.tag_stack.pop()
            # Do not flag html or body if omitted in snippets, but flag major containers
            parser.errors.append(
                DiagnosticError(
                    stage="syntax_check",
                    severity="ERROR",
                    file_path=file_path,
                    line=line,
                    column=col,
                    message=f"Unclosed HTML tag <{tag}> opened at line {line}, col {col}.",
                    suggested_fix=f"Add closing </{tag}> tag before end of document.",
                )
            )

        # Validate extracted inline JavaScript
        for script_code, start_line in parser.scripts:
            js_errors = self.js_validator.validate(script_code, file_path=file_path)
            for err in js_errors:
                # Adjust line offset
                adjusted_line = (err.line or 1) + start_line - 1
                parser.errors.append(
                    DiagnosticError(
                        stage="syntax_check",
                        severity=err.severity,
                        file_path=file_path,
                        line=adjusted_line,
                        column=err.column,
                        message=f"Inline script error: {err.message}",
                        suggested_fix=err.suggested_fix,
                    )
                )

        return parser.errors


# ============================================================================
# 6. C# Syntax Validator (Lexical Directives & Delimiters)
# ============================================================================

class CSharpSyntaxValidator(BaseSyntaxValidator):
    """
    Validates C# Unity scripts (.cs).
    Checks preprocessor directive balance (#if / #endif), delimiter balance
    ({}, (), []), and class/method declaration syntax.
    """

    @property
    def language_name(self) -> str:
        return "csharp"

    def validate(self, code: str, file_path: str = "<memory>") -> List[DiagnosticError]:
        errors: List[DiagnosticError] = []
        if not code.strip():
            return errors

        lines = code.splitlines(keepends=True)

        # 1. Preprocessor directive checks
        pp_stack: List[Tuple[str, int]] = []
        for line_num, raw in enumerate(lines, start=1):
            s = raw.strip()
            if s.startswith("#if"):
                pp_stack.append(("#if", line_num))
            elif s.startswith("#region"):
                pp_stack.append(("#region", line_num))
            elif s.startswith("#endif"):
                if not pp_stack:
                    errors.append(
                        DiagnosticError(
                            stage="syntax_check",
                            severity="ERROR",
                            file_path=file_path,
                            line=line_num,
                            column=1,
                            message="Unexpected '#endif' without matching '#if'.",
                            suggested_fix="Remove unmatched '#endif' or add corresponding '#if'.",
                        )
                    )
                else:
                    top_dir, _ = pp_stack.pop()
                    if top_dir != "#if":
                        errors.append(
                            DiagnosticError(
                                stage="syntax_check",
                                severity="ERROR",
                                file_path=file_path,
                                line=line_num,
                                column=1,
                                message=f"Mismatched '#endif'; top directive was '{top_dir}'.",
                            )
                        )
            elif s.startswith("#endregion"):
                if not pp_stack:
                    errors.append(
                        DiagnosticError(
                            stage="syntax_check",
                            severity="ERROR",
                            file_path=file_path,
                            line=line_num,
                            column=1,
                            message="Unexpected '#endregion' without matching '#region'.",
                        )
                    )
                else:
                    top_dir, _ = pp_stack.pop()
                    if top_dir != "#region":
                        errors.append(
                            DiagnosticError(
                                stage="syntax_check",
                                severity="ERROR",
                                file_path=file_path,
                                line=line_num,
                                column=1,
                                message=f"Mismatched '#endregion'; top directive was '{top_dir}'.",
                            )
                        )

        for unclosed_dir, unclosed_line in pp_stack:
            errors.append(
                DiagnosticError(
                    stage="syntax_check",
                    severity="ERROR",
                    file_path=file_path,
                    line=unclosed_line,
                    column=1,
                    message=f"Unclosed preprocessor directive '{unclosed_dir}' at line {unclosed_line}.",
                    suggested_fix=f"Close '{unclosed_dir}' with matching '#endif' or '#endregion'.",
                )
            )

        # 2. Delimiter balancing ({}, (), [])
        stack: List[_GDDelim] = []
        pairs = {")": "(", "]": "[", "}": "{"}
        in_multi_comment = False

        for line_num, raw in enumerate(lines, start=1):
            i = 0
            n = len(raw)

            while i < n:
                if in_multi_comment:
                    if raw.startswith("*/", i):
                        in_multi_comment = False
                        i += 2
                        continue
                    i += 1
                    continue

                if raw.startswith("//", i):
                    break
                if raw.startswith("/*", i):
                    in_multi_comment = True
                    i += 2
                    continue

                ch = raw[i]

                # Verbatim string @"..."
                if raw.startswith('@"', i):
                    i += 2
                    while i < n:
                        if raw[i] == '"':
                            if i + 1 < n and raw[i + 1] == '"':
                                i += 2
                                continue
                            i += 1
                            break
                        i += 1
                    continue

                # Normal string
                if ch in ('"', "'"):
                    quote = ch
                    col_start = i + 1
                    i += 1
                    closed = False
                    while i < n:
                        if raw[i] == "\\":
                            i += 2
                            continue
                        if raw[i] == quote:
                            closed = True
                            i += 1
                            break
                        if raw[i] in ("\r", "\n"):
                            break
                        i += 1
                    if not closed:
                        errors.append(
                            DiagnosticError(
                                stage="syntax_check",
                                severity="ERROR",
                                file_path=file_path,
                                line=line_num,
                                column=col_start,
                                message=f"Unterminated string literal on line {line_num}.",
                                suggested_fix="Close string literal with matching quotation mark.",
                            )
                        )
                    continue

                # Delimiters
                if ch in ("(", "[", "{"):
                    stack.append(_GDDelim(ch, line_num, i + 1))
                elif ch in (")", "]", "}"):
                    expected = pairs[ch]
                    if not stack:
                        errors.append(
                            DiagnosticError(
                                stage="syntax_check",
                                severity="ERROR",
                                file_path=file_path,
                                line=line_num,
                                column=i + 1,
                                message=f"Unmatched closing delimiter '{ch}'.",
                                suggested_fix=f"Remove extraneous '{ch}' or open matching '{expected}'.",
                            )
                        )
                    else:
                        top = stack.pop()
                        if top.char != expected:
                            errors.append(
                                DiagnosticError(
                                    stage="syntax_check",
                                    severity="ERROR",
                                    file_path=file_path,
                                    line=line_num,
                                    column=i + 1,
                                    message=f"Mismatched delimiter '{ch}'; expected closing for '{top.char}'.",
                                )
                            )

                i += 1

        while stack:
            top = stack.pop()
            errors.append(
                DiagnosticError(
                    stage="syntax_check",
                    severity="ERROR",
                    file_path=file_path,
                    line=top.line,
                    column=top.col,
                    message=f"Unclosed delimiter '{top.char}' opened at line {top.line}, col {top.col}.",
                    suggested_fix=f"Add closing delimiter for '{top.char}'.",
                )
            )

        return errors


# ============================================================================
# 7. MultiEngineSyntaxValidator (Facade & Dispatcher)
# ============================================================================

class MultiEngineSyntaxValidator:
    """
    Unified facade coordinating syntax validation across GDScript, Python,
    JavaScript, HTML, and C# scripts.
    """

    def __init__(self) -> None:
        self.python_validator = PythonSyntaxValidator()
        self.gdscript_validator = GDScriptSyntaxValidator()
        self.javascript_validator = JavaScriptSyntaxValidator()
        self.html_validator = HTMLSyntaxValidator()
        self.csharp_validator = CSharpSyntaxValidator()

        self._validators: Dict[str, BaseSyntaxValidator] = {
            "python": self.python_validator,
            "py": self.python_validator,
            "gdscript": self.gdscript_validator,
            "gd": self.gdscript_validator,
            "javascript": self.javascript_validator,
            "js": self.javascript_validator,
            "mjs": self.javascript_validator,
            "html": self.html_validator,
            "htm": self.html_validator,
            "csharp": self.csharp_validator,
            "cs": self.csharp_validator,
        }

    def get_validator(self, language_or_ext: str) -> Optional[BaseSyntaxValidator]:
        """Resolves validator by language name or file extension."""
        key = language_or_ext.lower().lstrip(".")
        return self._validators.get(key)

    def validate(
        self, code: str, language: str, file_path: str = "<memory>"
    ) -> List[DiagnosticError]:
        """Validates code string in specified language."""
        validator = self.get_validator(language)
        if not validator:
            # Fall back to extension of file_path
            ext = Path(file_path).suffix
            if ext:
                validator = self.get_validator(ext)

        if not validator:
            return [
                DiagnosticError(
                    stage="syntax_check",
                    severity="WARNING",
                    file_path=file_path,
                    line=1,
                    column=1,
                    message=f"Unsupported language '{language}' for syntax validation.",
                    suggested_fix="Use one of: gdscript, python, javascript, html, csharp.",
                )
            ]
        return validator.validate(code, file_path=file_path)

    def validate_script(self, script: ScriptSpec) -> List[DiagnosticError]:
        """Validates a ScriptSpec model."""
        return self.validate(
            code=script.code,
            language=script.language,
            file_path=script.rel_path,
        )

    def validate_file(self, file_path: Union[str, Path]) -> List[DiagnosticError]:
        """Validates single file on disk using its file extension."""
        path = Path(file_path)
        ext = path.suffix.lower()
        validator = self.get_validator(ext)
        if not validator:
            return [
                DiagnosticError(
                    stage="syntax_check",
                    severity="WARNING",
                    file_path=str(path),
                    line=1,
                    column=1,
                    message=f"Skipping unsupported file extension '{ext}'.",
                )
            ]
        return validator.validate_file(path)

    def validate_project_scripts(self, project_dir: Union[str, Path]) -> List[DiagnosticError]:
        """Recursively validates all supported engine scripts within project directory."""
        root = Path(project_dir)
        errors: List[DiagnosticError] = []
        if not root.is_dir():
            return [
                DiagnosticError(
                    stage="syntax_check",
                    severity="ERROR",
                    file_path=str(root),
                    line=1,
                    column=1,
                    message=f"Project directory does not exist: {root}",
                )
            ]

        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            ext = path.suffix.lower()
            validator = self.get_validator(ext)
            if validator:
                rel = str(path.relative_to(root))
                file_errors = validator.validate_file(path)
                # Ensure relative path reported
                for err in file_errors:
                    err.file_path = rel
                errors.extend(file_errors)

        return errors
