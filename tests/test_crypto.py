"""The relay's primitives against their RFCs' test vectors, and a sealed record that fails to open once any part
of it changes."""

from __future__ import annotations

import unittest
from unittest import mock

from projector import crypto

ALICE = bytes.fromhex("77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a")
BOB = bytes.fromhex("5dab087e624a8a4b79e17f8b83800ee66f3bb1292618b6fd1c2f8b27ff88e0eb")
SUNSCREEN = (b"Ladies and Gentlemen of the class of '99: If I could offer you only one tip for the future, "
             b"sunscreen would be it.")


class PrimitiveTests(unittest.TestCase):
    def test_x25519_matches_rfc_7748_section_6_1(self) -> None:
        alice, bob = crypto.public_key(ALICE), crypto.public_key(BOB)

        self.assertEqual("8520f0098930a754748b7ddcb43ef75a0dbf3a0d26381af4eba4a98eaa9b4e6a", alice.hex())
        self.assertEqual("de9edb7d7b7dc1b4d35b61c2ece435373f8343c85b78674dadfc7e146f882b4f", bob.hex())
        self.assertEqual("4a5d9d5ba4ce2de1728e3bf480350f25e07e21c947d19e3376f09b3c1e161742",
                         crypto.x25519(ALICE, bob).hex())
        self.assertEqual(crypto.x25519(ALICE, bob), crypto.x25519(BOB, alice))

    def test_chacha20_matches_rfc_8439_section_2_4_2(self) -> None:
        nonce = bytes.fromhex("000000000000004a00000000")

        encrypted = crypto.chacha20(bytes(range(32)), 1, nonce, SUNSCREEN)

        self.assertEqual(
            "6e2e359a2568f98041ba0728dd0d6981e97e7aec1d4360c20a27afccfd9fae0bf91b65c5524733ab8f593dabcd62b357"
            "1639d624e65152ab8f530c359f0861d807ca0dbf500d6a6156a38e088a22b65e52bc514d16ccf806818ce91ab7793736"
            "5af90bbf74a35be6b40b8eedf2785e42874d", encrypted.hex())
        self.assertEqual(SUNSCREEN, crypto.chacha20(bytes(range(32)), 1, nonce, encrypted))

    def test_hkdf_matches_rfc_5869_test_case_1(self) -> None:
        derived = crypto.hkdf(bytes([0x0B]) * 22, salt=bytes.fromhex("000102030405060708090a0b0c"),
                              info=bytes.fromhex("f0f1f2f3f4f5f6f7f8f9"), length=42)

        self.assertEqual("3cb25f25faacd57a90434f64d0362f2a2d2d0a90cf1a5a4c5db02d56ecc4c5bf34007208d5b887185865",
                         derived.hex())

    def test_a_low_order_public_key_is_refused_rather_than_agreeing_on_zero(self) -> None:
        with self.assertRaises(crypto.CryptoError):
            crypto.agree(ALICE, bytes(32))


class SealTests(unittest.TestCase):
    def setUp(self) -> None:
        self.recipient = crypto.public_key(BOB)
        self.sealed = crypto.seal(ALICE, self.recipient, b'{"text": "Which error type?"}')

    def test_a_sealed_record_opens_to_its_plaintext_and_its_senders_key(self) -> None:
        self.assertEqual((crypto.public_key(ALICE), b'{"text": "Which error type?"}'),
                         crypto.unseal(BOB, self.sealed))

    def test_sealing_one_record_twice_gives_two_ciphertexts(self) -> None:
        again = crypto.seal(ALICE, self.recipient, b'{"text": "Which error type?"}')

        self.assertNotEqual(self.sealed["ciphertext"], again["ciphertext"])

    def test_changing_any_byte_the_tag_covers_makes_the_record_fail_to_open(self) -> None:
        for field in ("ciphertext", "tag", "sender", "ephemeral"):
            with self.subTest(field):
                data = bytearray(crypto.decode(self.sealed[field]))
                data[-1] ^= 1
                altered = {**self.sealed, field: crypto.encode(bytes(data))}

                with self.assertRaises(crypto.CryptoError):
                    crypto.unseal(BOB, altered)

    def test_a_record_tagged_under_another_mac_key_fails_even_with_the_right_cipher_key(self) -> None:
        derive = crypto.derive

        def other_mac_key(*args: bytes) -> tuple[bytes, bytes]:
            cipher_key, mac_key = derive(*args)
            return cipher_key, bytes([mac_key[0] ^ 1]) + mac_key[1:]

        with mock.patch.object(crypto, "derive", other_mac_key):
            sealed = crypto.seal(ALICE, self.recipient, b'{"text": "Which error type?"}')

        with self.assertRaises(crypto.CryptoError):
            crypto.unseal(BOB, sealed)

    def test_a_record_sealed_to_another_key_does_not_open(self) -> None:
        with self.assertRaises(crypto.CryptoError):
            crypto.unseal(ALICE, self.sealed)


if __name__ == "__main__":
    unittest.main()
