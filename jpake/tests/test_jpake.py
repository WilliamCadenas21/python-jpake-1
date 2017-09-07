import unittest

from collections import abc

from jpake import JPAKE
from jpake.exceptions import OutOfSequenceError, DuplicateSignerError


class JPAKETestCase(unittest.TestCase):
    def test_basic(self):
        secret = "hunter42"
        alice = JPAKE(secret=secret, signer_id=b"alice")
        bob = JPAKE(secret=secret, signer_id=b"bob")

        alice.process_one(bob.one()), bob.process_one(alice.one())

        alice.process_two(bob.two()), bob.process_two(alice.two())

        self.assertEqual(alice.K, bob.K)

    def test_skip_verification_one(self):
        secret = "hunter42"
        alice = JPAKE(secret=secret, signer_id=b"alice")
        bob = JPAKE(secret=secret, signer_id=b"bob")

        bob_one = bob.one()

        alice.process_one(
            remote_gx1=bob_one['gx1'],
            remote_gx2=bob_one['gx2'],
            verify=False,
        )

    def test_no_proofs_one(self):
        secret = "hunter42"
        alice = JPAKE(secret=secret, signer_id=b"alice")
        bob = JPAKE(secret=secret, signer_id=b"bob")

        bob_one = bob.one()

        self.assertRaises(
            Exception, alice.process_one,
            remote_gx1=bob_one['gx1'], remote_gx2=bob_one['gx2']
        )

    def test_skip_verification_on_dict(self):
        secret = "hunter42"
        alice = JPAKE(secret=secret, signer_id=b"alice")
        bob = JPAKE(secret=secret, signer_id=b"bob")

        self.assertRaises(
            ValueError, alice.process_one, bob.one(), verify=False
        )

    def test_secret_after_process_one(self):
        secret = "hunter42"
        alice = JPAKE(signer_id=b"alice")
        bob = JPAKE(signer_id=b"bob")

        alice.process_one(bob.one()), bob.process_one(alice.one())

        alice.set_secret(secret)
        bob.set_secret(secret)

        alice.process_two(bob.two()), bob.process_two(alice.two())

        self.assertEqual(alice.K, bob.K)

    def test_get_two_before_secret(self):
        alice = JPAKE(signer_id=b"alice")
        bob = JPAKE(signer_id=b"bob")

        bob.process_one(alice.one())

        self.assertRaises(OutOfSequenceError, bob.two)

    def test_get_two_before_process_one(self):
        alice = JPAKE(signer_id=b"alice")

        alice.set_secret("hunter 2")

        self.assertRaises(OutOfSequenceError, alice.two)

    def test_process_two_before_process_one(self):
        secret = "hunter42"
        alice = JPAKE(secret=secret, signer_id=b"alice")
        bob = JPAKE(secret=secret, signer_id=b"bob")

        bob.process_one(alice.one())

        bob_two = bob.two()

        self.assertRaises(OutOfSequenceError, alice.process_two, bob_two)

    def test_get_secret_before_secret(self):
        alice = JPAKE()

        with self.assertRaises(AttributeError):
            alice.secret

    def test_get_A_before_process_one(self):
        alice = JPAKE()

        alice.set_secret("hunter 2")

        with self.assertRaises(AttributeError):
            alice.A

    def test_get_A_before_secret(self):
        alice = JPAKE(signer_id=b'alice')
        bob = JPAKE(signer_id=b'bob')

        alice.process_one(bob.one())

        with self.assertRaises(AttributeError):
            alice.A

    def test_get_A(self):
        alice = JPAKE(signer_id=b'alice')
        bob = JPAKE(signer_id=b'bob')

        alice.set_secret("hunter 2")
        alice.process_one(bob.one())

        self.assertIsInstance(alice.A, int)

    def test_get_zkp_A_before_process_one(self):
        alice = JPAKE()

        alice.set_secret("hunter 2")

        with self.assertRaises(AttributeError):
            alice.zkp_A

    def test_get_zkp_A_before_secret(self):
        alice = JPAKE(signer_id=b'alice')
        bob = JPAKE(signer_id=b'bob')

        alice.process_one(bob.one())

        with self.assertRaises(AttributeError):
            alice.zkp_A

    def test_get_zkp_A(self):
        alice = JPAKE(signer_id=b'alice')
        bob = JPAKE(signer_id=b'bob')

        alice.set_secret("hunter 2")
        alice.process_one(bob.one())

        self.assertIsInstance(alice.zkp_A, abc.Mapping)
        self.assertEqual(set(alice.zkp_A.keys()), {'id', 'gr', 'b'})
        self.assertEqual(alice.zkp_A['id'], b'alice')
        self.assertIsInstance(alice.zkp_A['gr'], int)
        self.assertIsInstance(alice.zkp_A['b'], int)

    def test_set_secret_twice(self):
        secret = "hunter42"
        alice = JPAKE()
        alice.set_secret(secret)
        self.assertRaises(OutOfSequenceError, alice.set_secret, secret)

    def test_process_one_twice(self):
        secret = "hunter42"
        alice = JPAKE(secret=secret, signer_id=b"alice")
        bob = JPAKE(secret=secret, signer_id=b"bob")

        alice_one = alice.one()

        bob.process_one(alice_one)
        self.assertRaises(OutOfSequenceError, bob.process_one, alice_one)

    def test_process_two_twice(self):
        secret = "hunter42"
        alice = JPAKE(secret=secret, signer_id=b"alice")
        bob = JPAKE(secret=secret, signer_id=b"bob")

        alice.process_one(bob.one()), bob.process_one(alice.one())

        bob_two = bob.two()

        alice.process_two(bob_two)
        self.assertRaises(OutOfSequenceError, alice.process_two, bob_two)

    def test_duplicate_signer_id(self):
        secret = "hunter42"
        alice = JPAKE(secret=secret, signer_id=b"alice")
        mallory = JPAKE(secret=secret, signer_id=b"alice")

        with self.assertRaises(DuplicateSignerError):
            alice.process_one(mallory.one())
