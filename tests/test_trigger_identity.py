from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from context_image.errors import IdentityRequiredError
from context_image.identity import IdentityStore
from context_image.models import (
    GenerationMode,
    GenerationRequest,
    ReferenceRole,
    Subject,
)
from context_image.trigger import (
    is_explicit_image_request,
    resolve_mode,
    resolve_subject,
)


class TriggerTests(TestCase):
    def test_bot_photo_request_uses_identity_mode(self):
        request = GenerationRequest.from_mapping(
            {"request": "拍一张你现在的照片"}
        )

        subject = resolve_subject(request, ["你", "麦麦", "MaiBot"])
        mode = resolve_mode(
            request,
            subject,
            has_base_image=False,
            identity_enabled=True,
        )

        self.assertIs(subject, Subject.BOT)
        self.assertIs(mode, GenerationMode.IDENTITY_REFERENCE)

    def test_capability_question_is_not_explicit_request(self):
        self.assertFalse(is_explicit_image_request("你会拍照吗"))

    def test_negative_request_is_not_explicit_request(self):
        self.assertFalse(is_explicit_image_request("不要生成图片"))

    def test_regular_image_request_is_explicit(self):
        self.assertTrue(is_explicit_image_request("画一只在月球喝咖啡的猫"))

    def test_explicit_subject_wins_over_alias_fallback(self):
        request = GenerationRequest.from_mapping(
            {"request": "画一下你", "subject": "other"}
        )

        self.assertIs(resolve_subject(request, ["你"]), Subject.OTHER)

    def test_polite_addressee_pronoun_is_not_the_visual_subject(self):
        request = GenerationRequest.from_mapping(
            {"request": "请你帮我生成一张猫的图片"}
        )

        self.assertIs(resolve_subject(request, ["你"]), Subject.OTHER)

    def test_polite_location_addressee_is_not_the_visual_subject(self):
        request = GenerationRequest.from_mapping(
            {"request": "请你在海边生成一张猫的照片"}
        )

        self.assertIs(resolve_subject(request, ["你"]), Subject.OTHER)

    def test_pronoun_location_after_image_verb_is_the_visual_subject(self):
        request = GenerationRequest.from_mapping(
            {"request": "生成一张你在海边的照片"}
        )

        self.assertIs(resolve_subject(request, ["你"]), Subject.BOT)

    def test_latin_alias_requires_token_boundaries(self):
        request = GenerationRequest.from_mapping(
            {"request": "请生成一张robot的海报"}
        )

        self.assertIs(resolve_subject(request, ["bot"]), Subject.OTHER)

    def test_cjk_alias_rejects_embedded_name_but_accepts_subject_syntax(self):
        embedded = GenerationRequest.from_mapping(
            {"request": "生成小麦田的风景照片"}
        )
        outfit = GenerationRequest.from_mapping(
            {"request": "我想看小麦穿汉服"}
        )
        scene = GenerationRequest.from_mapping(
            {"request": "生成小麦在海边的照片"}
        )

        self.assertIs(resolve_subject(embedded, ["小麦"]), Subject.OTHER)
        self.assertIs(resolve_subject(outfit, ["小麦"]), Subject.BOT)
        self.assertIs(resolve_subject(scene, ["小麦"]), Subject.BOT)

    def test_base_image_uses_image_to_image(self):
        request = GenerationRequest.from_mapping({"request": "改成下雪的夜晚"})

        mode = resolve_mode(
            request,
            Subject.OTHER,
            has_base_image=True,
            identity_enabled=True,
        )

        self.assertIs(mode, GenerationMode.IMAGE_TO_IMAGE)


class IdentityTests(TestCase):
    def test_missing_identity_image_raises_safe_error(self):
        with TemporaryDirectory() as directory:
            store = IdentityStore(Path(directory), "bot-face.png", 20 * 1024 * 1024)

            with self.assertRaises(IdentityRequiredError):
                store.load()

    def test_loads_png_identity_from_scoped_directory(self):
        with TemporaryDirectory() as directory:
            identity_dir = Path(directory) / "identity"
            identity_dir.mkdir()
            data = b"\x89PNG\r\n\x1a\nidentity"
            (identity_dir / "bot-face.png").write_bytes(data)
            store = IdentityStore(Path(directory), "bot-face.png", 1024)

            reference = store.load()

            self.assertEqual(reference.data, data)
            self.assertEqual(reference.mime_type, "image/png")
            self.assertIs(reference.role, ReferenceRole.IDENTITY)
            self.assertEqual(reference.source, "identity:bot")

    def test_rejects_path_traversal_filename(self):
        with TemporaryDirectory() as directory:
            store = IdentityStore(Path(directory), "../face.png", 1024)

            with self.assertRaises(IdentityRequiredError):
                store.load()
