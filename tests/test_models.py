import unittest

from pydantic import ValidationError

from app.models import (
    AssistantConversationMessage,
    Chat,
    Dimensions,
    User,
    UserConversationMessage,
    UserCreate,
)


class UserCreateTests(unittest.TestCase):
    def test_accepts_a_strong_password(self) -> None:
        user = UserCreate(email="editor@example.com", password="StrongPass1!")

        self.assertEqual(user.email, "editor@example.com")

    def test_rejects_each_missing_password_character_class(self) -> None:
        invalid_passwords = (
            "lowercase1!",
            "UPPERCASE1!",
            "NoNumber!",
            "NoSpecial1",
        )

        for password in invalid_passwords:
            with self.subTest(password=password), self.assertRaises(ValidationError):
                UserCreate(email="editor@example.com", password=password)


class SerializationTests(unittest.TestCase):
    def test_user_serialization_never_exposes_password_hash(self) -> None:
        user = User(
            user_id="user-1",
            email="editor@example.com",
            hashed_password="$2b$12$not-a-real-hash",
        )

        self.assertNotIn("hashed_password", user.model_dump())
        self.assertNotIn("hashed_password", user.model_dump_json())


class ModelDefaultTests(unittest.TestCase):
    def test_conversation_file_lists_are_not_shared(self) -> None:
        first = UserConversationMessage(timestamp="2026-01-01T00:00:00Z")
        second = UserConversationMessage(timestamp="2026-01-01T00:00:00Z")
        assistant = AssistantConversationMessage(timestamp="2026-01-01T00:00:00Z")

        first.input_files.append(None)  # type: ignore[arg-type]

        self.assertEqual(second.input_files, [])
        self.assertEqual(assistant.output_files, [])

    def test_chat_conversations_are_not_shared(self) -> None:
        first = Chat(chat_id="chat-1", user_id="user-1")
        second = Chat(chat_id="chat-2", user_id="user-1")

        first.conversations.append({"role": "user"})

        self.assertEqual(second.conversations, [])

    def test_dimensions_must_be_positive(self) -> None:
        with self.assertRaises(ValidationError):
            Dimensions(width=0, height=1080)


if __name__ == "__main__":
    unittest.main()
