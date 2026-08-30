"""Tests des utilitaires transverses (identifiants, hachage, numérotation)."""

from __future__ import annotations

import pytest

from bldp.utils import (
    hash_file,
    hash_text,
    human_size,
    make_article_id,
    make_document_id,
    parse_number,
    roman_to_int,
    read_jsonl,
    slugify,
    write_jsonl,
)


class TestSlugify:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("Code du Travail", "code_du_travail"),
            ("Loi n° 2026-001 du 10 février", "loi_n_2026_001_du_10_fevrier"),
            ("Bénin — Décret", "benin_decret"),
            ("   ", "document"),
        ],
    )
    def test_slugify(self, raw, expected):
        assert slugify(raw) == expected

    def test_slugify_truncates(self):
        assert len(slugify("a" * 200)) <= 80


class TestIdentifiers:
    def test_document_id_from_filename(self):
        assert make_document_id("Code du Travail.pdf", "abc123") == "code_du_travail"

    def test_document_id_collision_is_disambiguated(self):
        first = make_document_id("code.pdf", "aaaaaaaa11")
        second = make_document_id("code.pdf", "bbbbbbbb22", existing={first})
        assert second != first
        assert second.startswith("code_")

    def test_document_id_is_deterministic(self):
        args = ("code.pdf", "bbbbbbbb22", {"code"})
        assert make_document_id(*args) == make_document_id(*args)

    def test_article_id_format(self):
        assert make_article_id("code_travail", "45", 0) == "code_travail_article_45"
        assert make_article_id("code_travail", "45 bis", 3) == "code_travail_article_45_bis"


class TestNumbering:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("45", 45.0),
            ("1er", 1.0),
            ("premier", 1.0),
            ("Premier", 1.0),
            ("2ème", 2.0),
            ("XII", 12.0),
            ("iv", 4.0),
            ("45 bis", 45.1),
            ("45 ter", 45.2),
            ("45-2", 45.02),
        ],
    )
    def test_parse_number(self, raw, expected):
        assert parse_number(raw) == pytest.approx(expected)

    @pytest.mark.parametrize("raw", ["", "   ", "abc", None])
    def test_unparseable_returns_none(self, raw):
        """En cas de doute, on ne devine pas : la valeur reste indéterminée."""
        assert parse_number(raw) is None

    def test_bis_sorts_after_base_number(self):
        assert parse_number("45") < parse_number("45 bis") < parse_number("46")

    def test_roman_to_int(self):
        assert roman_to_int("XIV") == 14
        assert roman_to_int("MCMXC") == 1990
        assert roman_to_int("hello") is None


class TestHashing:
    def test_text_hash_ignores_formatting(self):
        assert hash_text("Article   45\n\nLe salarié") == hash_text("article 45 le salarié")

    def test_text_hash_differs_on_content(self):
        assert hash_text("Article 45") != hash_text("Article 46")

    def test_file_hash(self, tmp_path):
        first = tmp_path / "a.bin"
        second = tmp_path / "b.bin"
        first.write_bytes(b"contenu identique")
        second.write_bytes(b"contenu identique")
        assert hash_file(first) == hash_file(second)


class TestIO:
    def test_jsonl_roundtrip(self, tmp_path):
        target = tmp_path / "out" / "records.jsonl"
        records = [{"id": 1, "text": "Article 1er"}, {"id": 2, "text": "Article 2"}]
        assert write_jsonl(target, records) == 2
        assert list(read_jsonl(target)) == records

    def test_human_size(self):
        assert human_size(512) == "512 o"
        assert human_size(2048).startswith("2.0 Kio")
