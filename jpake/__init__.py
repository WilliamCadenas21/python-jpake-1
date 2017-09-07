from types import MappingProxyType
from random import SystemRandom
from hashlib import sha1

from jpake.parameters import NIST_80, NIST_112, NIST_128

from jpake.exceptions import (
    DuplicateSignerError, InvalidProofError, OutOfSequenceError,
)


def _from_bytes(bs):
    return int.from_bytes(bs, 'big')


def _to_bytes(num):
    return num.to_bytes((num.bit_length() // 8) + 1, byteorder='big')


def _default_zkp_hash_fn(*, g, gr, gx, signer_id):
    """
    Implementation of the zero knowledge proof hash algorithm used by openSSL.

    https://github.com/openssl/openssl/blob/master/crypto/jpake/jpake.c#L166
    """
    def pascal(s):
        """
        Encode a byte string as a pascal string with a big-endian header
        """
        if len(s) >= 2**16:
            raise ValueError("cannot encode value greater than (2^8)^(2^16)")
        return len(s).to_bytes(2, 'big') + s

    s = b"".join((
        pascal(_to_bytes(g)),
        pascal(_to_bytes(gr)),
        pascal(_to_bytes(gx)),
        pascal(signer_id)
    ))
    return _from_bytes(sha1(s).digest())


class JPAKE(object):
    __slots__ = [
        '_rng', '_zkp_hash',
        'waiting_secret', 'waiting_one', 'waiting_two',
        'p', 'g', 'q',
        '_secret', 'signer_id',
        '_A', '_zkp_A',
        'x1', 'x2', '_gx1', '_gx2', '_zkp_x1', '_zkp_x2',
        '_remote_A', '_remote_zkp_A',
        '_remote_gx1', '_remote_gx2', '_remote_zkp_x1', '_remote_zkp_x2',
        '_K',
    ]

    # Variables set at initialisation.
    @property
    def secret(self):
        """The shared secret.

        Set during initialisation or by calling by :meth:`set_secret`.

        :type: int
        """
        if self.waiting_secret:
            raise AttributeError("secret not set")
        return self._secret

    @property
    def gx1(self):
        """:math:`g^x1`
        :type: int
        """
        if not hasattr(self, '_gx1'):
            self._compute_one()
        return self._gx1

    @property
    def gx2(self):
        """:math:`g^x2`
        :type: int
        """
        if not hasattr(self, '_gx2'):
            self._compute_one()
        return self._gx2

    @property
    def zkp_x1(self):
        """Proof of knowledge of :math:`x1`
        """
        if not hasattr(self, '_zkp_x1'):
            self._compute_one()
        return self._zkp_x1

    @property
    def zkp_x2(self):
        """Proof of knowledge of :math:`x2`
        """
        if not hasattr(self, '_zkp_x2'):
            self._compute_one()
        return self._zkp_x2

    # Variables sent by the other participant for phase one.
    @property
    def remote_gx1(self):
        """
        :math:`g^x3`
        :type: int
        """
        if self.waiting_one:
            raise AttributeError()
        return self._remote_gx1

    @property
    def remote_gx2(self):
        """
        :math:`g^x4`
        :type: int
        """
        if self.waiting_one:
            raise AttributeError()
        return self._remote_gx2

    @property
    def remote_zkp_x1(self):
        """
        Proof of knowledge of :math:`x3`.
        """
        if self.waiting_one:
            raise AttributeError()
        return self._remote_zkp_x1

    @property
    def remote_zkp_x2(self):
        """
        Proof of knowledge of :math:`x4`.
        """
        if self.waiting_one:
            raise AttributeError()
        return self._remote_zkp_x2

    # Variables that can be computed after receiving the phase one data from
    # the other participant.
    @property
    def A(self):
        """
        :math:`g^((x3+x4+x1)*x2*s)`
        """
        if not hasattr(self, '_A'):
            try:
                self._compute_two()
            except OutOfSequenceError as e:
                raise AttributeError("A is not available yet") from e
        return self._A

    @property
    def zkp_A(self):
        """
        Proof of knowledge of :math:`x2*s`
        """
        if not hasattr(self, '_zkp_A'):
            try:
                self._compute_two()
            except OutOfSequenceError as e:
                raise AttributeError("zkp_A is not available yet") from e
        return self._zkp_A

    # Variables sent by the other participant for phase two.
    @property
    def remote_A(self):
        """
        :math:`g^(x1+x2+x3)*x4*s`
        """
        if self.waiting_two:
            raise AttributeError()
        return self._remote_A

    @property
    def remote_zkp_A(self):
        """
        Proof of knowledge of :math:`x4*s`.
        """
        if self.waiting_two:
            raise AttributeError()
        return self._remote_zkp_A

    # The agreed key.
    @property
    def K(self):
        if not hasattr(self, '_K'):
            try:
                self._compute_three()
            except OutOfSequenceError as e:
                raise AttributeError("K is not available yet") from e
        return self._K

    def __init__(
        self, *, x1=None, x2=None, secret=None,
        remote_gx1=None, remote_gx2=None, remote_A=None,
        parameters=NIST_128, signer_id=None,
        zkp_hash_function=None, random=None
    ):
        if random is None:
            random = SystemRandom()
        self._rng = random

        if zkp_hash_function is None:
            zkp_hash_function = _default_zkp_hash_fn
        self._zkp_hash = zkp_hash_function

        self.waiting_secret = True
        self.waiting_one = True
        self.waiting_two = True

        if isinstance(signer_id, str):
            signer_id = signer_id.encode('utf-8')
        if signer_id is None:
            signer_id = _to_bytes(self._rng.getrandbits(16))
        self.signer_id = signer_id

        self.p = parameters.p
        self.g = parameters.g
        self.q = parameters.q

        # Setup hidden state
        if x1 is None:
            x1 = self._rng.randrange(self.q)
        self.x1 = x1

        if x2 is None:
            x2 = self._rng.randrange(1, self.q)
        self.x2 = x2

        # Resume from after step one
        if remote_gx1 is not None and remote_gx2 is None:
            raise TypeError("only remote_gx1 provided")
        if remote_gx1 is None and remote_gx2 is not None:
            raise TypeError("only remote_gx2 provided")

        if remote_gx1 is not None:
            self.process_one(
                remote_gx1=remote_gx1,
                remote_gx2=remote_gx2,
                verify=False,
            )

        # Resume from after setting secret
        if secret is not None:
            self.set_secret(secret)

        # Resume from after step two
        if remote_A is not None:
            self.process_two(remote_A=remote_A, verify=False)

    def _zkp(self, generator, exponent, gx=None):
        """
        Returns a proof that can be used by someone who only has knowledge
        of ``generator`` and ``p`` that we have a value for ``exponent`` that
        satisfies the equation ``generator^exponent=B mod p``
        """
        p = self.p
        q = self.q

        if gx is None:
            gx = pow(generator, exponent, p)
        r = self._rng.randrange(q)
        gr = pow(generator, r, p)
        h = self._zkp_hash(
            g=generator, gr=gr, gx=gx, signer_id=self.signer_id
        )
        b = (r - exponent*h) % q
        return {
            'gr': gr,
            'b': b,
            'id': self.signer_id,
        }

    def _verify_zkp(self, generator, gx, zkp):
        """Verify that the senders proof that they know ``x`` such that
        ``generator^{x} mod p = gx`` holds.
        """
        p = self.p
        gr = zkp['gr']
        b = zkp['b']

        if zkp['id'] == self.signer_id:
            raise DuplicateSignerError(zkp['id'])
        h = self._zkp_hash(
            g=generator, gr=gr, gx=gx, signer_id=zkp['id']
        )
        gb = pow(generator, b, p)
        y = pow(gx, h, p)
        if gr != (gb*y) % p:
            raise InvalidProofError()

    def set_secret(self, value):
        if not self.waiting_secret:
            raise OutOfSequenceError("secret already set")

        if value is None:
            raise ValueError()

        # TODO TODO TODO this is probably not the correct behaviour
        if isinstance(value, str):
            value = value.encode('utf-8')
        if isinstance(value, bytes):
            value = _from_bytes(value)

        self._secret = value
        self.waiting_secret = False

    def _compute_one(self):
        self._gx1 = pow(self.g, self.x1, self.p)
        self._gx2 = pow(self.g, self.x2, self.p)

        self._zkp_x1 = MappingProxyType(self._zkp(self.g, self.x1, self.gx1))
        self._zkp_x2 = MappingProxyType(self._zkp(self.g, self.x2, self.gx2))

    def one(self):
        self._compute_one()
        return {
            'gx1': self.gx1,
            'zkp_x1': dict(self.zkp_x1),
            'gx2': self.gx2,
            'zkp_x2': dict(self.zkp_x2),
        }

    def process_one(
        self, data=None, *,
        remote_gx1=None, remote_gx2=None,
        remote_zkp_x1=None, remote_zkp_x2=None,
        verify=True
    ):
        """
        Read in and verify the result of step one as sent by the other party.

        Accepts either a dictionary of values in the form produced by ``one``
        or the required values passed in individually as keyword arguments.

        :param data:
            A dictionary containing the results of running step one at
            the other end of the connection.

        :param remote_gx1:
            :math:`g^x3`
        :param remote_gx2:
            :math:`g^x4`
        :param remote_zkp_x1:
            Proof that ``x3`` is known by the caller.
        :param remote_zkp_x2:
            Proof that ``x4`` is known by the caller.

        :param verify:
            If ``False`` then ``remote_zkp_x1`` and ``remote_zkp_x2`` are
            ignored and proof verification is skipped.  This is a bad idea
            unless ``remote_gx1`` and ``remote_gx2`` have already been verified
            and is disallowed entirely if arguments are passed in a ``dict``.

        :raises OutOfSequenceError:
            If called more than once.
        :raises InvalidProofError:
            If verification is enabled and either of the proofs fail
        """
        p = self.p
        g = self.g

        if not self.waiting_one:
            raise OutOfSequenceError("step one already processed")

        if data is not None:
            if any(
                param is not None
                for param in (
                    remote_gx1, remote_gx2, remote_zkp_x1, remote_zkp_x2,
                )
            ):
                raise TypeError("unexpected keyword argument")

            if not verify:
                raise ValueError("dicts should always be verified")

            remote_gx1 = data['gx1']
            remote_gx2 = data['gx2']

            remote_zkp_x1 = data['zkp_x1']
            remote_zkp_x2 = data['zkp_x2']

        # we need to at least check this for ``remote_gx2`` in order to prevent
        # callers sneaking in ``remote_gx2 mod p`` equal to 1
        remote_gx1 %= p
        remote_gx2 %= p

        if remote_gx2 == 1:
            raise ValueError("remote_gx2 must not be one")

        if verify:
            if remote_zkp_x1 is None or remote_zkp_x2 is None:
                raise TypeError("expected zero knowledge proofs")
            self._verify_zkp(g, remote_gx1, remote_zkp_x1)
            self._verify_zkp(g, remote_gx2, remote_zkp_x2)

        self._remote_gx1 = remote_gx1
        self._remote_gx2 = remote_gx2

        self._remote_zkp_x1 = remote_zkp_x1
        self._remote_zkp_x2 = remote_zkp_x2

        self.waiting_one = False

    def _compute_two(self):
        if self.waiting_one:
            raise OutOfSequenceError(
                "can't compute step two without results from one"
            )

        if self.waiting_secret:
            raise OutOfSequenceError(
                "can't compute step two without secret"
            )

        p = self.p

        remote_gx1 = self.remote_gx1
        remote_gx2 = self.remote_gx2

        # A = g^((x1+x3+x4)*x2*s)
        #   = (g^x1*g^x3*g^x4)^(x2*s)
        t1 = (((self.gx1 * remote_gx1) % p) * remote_gx2) % p
        t2 = (self.x2 * self.secret) % p

        A = pow(t1, t2, p)

        # zero knowledge proof for ``x2*s``
        zkp_A = self._zkp(t1, t2, A)

        self._A = A
        self._zkp_A = MappingProxyType(zkp_A)

    def two(self):
        self._compute_two()
        return {
            'A': self.A,
            'zkp_A': dict(self.zkp_A),
        }

    def process_two(
        self, data=None, *,
        remote_A=None, remote_zkp_A=None,
        verify=True
    ):
        """
        Read in and verify the result of performing step two on the other end
        of the connection.

        :param data:
            A dictionary containing the results of running step two at the
            other end of the connection.

        :param remote_A:
            :math:`g^((x1+x2+x3)*x4*s)`
        :param remote_zkp_A:
            Proof that :math:`x4*s` is known by the caller.

        :param verify:
            If ``False`` then ``remote_zkp_A`` is ignored and proof
            verification is skipped.  This is a bad idea unless ``remote_A``
            has already been verified.

        :raises OutOfSequenceError:
            If called more than once or before ``process_one``.
        :raises InvalidProofError:
            If verification is enabled and either of the proofs fail.
        """
        p = self.p

        if self.waiting_one:
            raise OutOfSequenceError("step two cannot be processed before one")

        if not self.waiting_two:
            raise OutOfSequenceError("step two already processed")

        if data is not None:
            if remote_A is not None or remote_zkp_A is not None:
                raise TypeError("unexpected keyword argument")
            remote_A = data['A']
            remote_zkp_A = data['zkp_A']

        if verify:
            generator = (((self.gx1*self.gx2) % p) * self.remote_gx1) % p
            self._verify_zkp(generator, remote_A, remote_zkp_A)

        self._remote_A = remote_A
        self._remote_zkp_A = remote_zkp_A

        self.waiting_two = False

    def _compute_three(self):
        if self.waiting_two:
            raise OutOfSequenceError(
                "can't compute step three without results from two"
            )

        p = self.p
        q = self.q

        # t3 = g^-(x4*x2*s)
        #    = (g^x4)^(x2*-s)
        bottom = pow(self.remote_gx2, self.x2 * (q - self.secret), p)

        # t4 = B/(g^(x4*x2*s))
        #    = B*t3
        inner = (self.remote_A * bottom) % p

        # K = (B/(g^(x4*x2*s)))^x2
        K = pow(inner, self.x2, p)

        # TODO Key derivation function is necessary to avoid exposing K but the
        # spec does not fix one and the choice of function depends on the
        # application.  Possibly choose one that can be adjusted to output a
        # key of approximately the same number of bits
        self._K = K


__all__ = ['NIST_80', 'NIST_112', 'NIST_128', 'JPAKE']
