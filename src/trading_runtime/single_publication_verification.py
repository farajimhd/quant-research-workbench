"""Bounded replay memo kernel; never issues source or financial authority.

The integrating issuer must supply complete immutable input/output images and
dependency checks. This kernel is not yet selected by any installed strategy.
"""
from contextlib import contextmanager
from threading import current_thread


class SinglePublicationVerification:
    def __init__(self, *, original, input_image, output_image, require_authority,
                 max_image_bytes):
        if type(max_image_bytes) is not int or max_image_bytes < 1:
            raise ValueError('An explicit positive image budget is required')
        self._callbacks = original, input_image, output_image, require_authority
        if any(not callable(value) for value in self._callbacks):
            raise ValueError('Complete original verifier and guards are required')
        self._codes = self._code_image()
        self._max_bytes = max_image_bytes
        self._active = False
        self._owner = None
        self._input = self._output = self._result = None
        self._has_result = False
        self._entered = False

    def _code_image(self):
        return tuple((getattr(value, '__func__', value),
                      getattr(getattr(value, '__func__', value), '__code__', None))
                     for value in self._callbacks)

    def _image(self, value):
        if type(value) is not bytes or len(value) > self._max_bytes:
            raise ValueError('Verification image is not immutable bounded bytes')
        return value

    def _require(self):
        if not self._active or self._owner is not current_thread():
            raise ValueError('Publication verification scope is inactive or foreign')
        if self._codes != self._code_image():
            raise ValueError('Publication verification callback code changed')
        self._callbacks[3]()
        if self._codes != self._code_image():
            raise ValueError('Publication authority changed callback code')

    @contextmanager
    def scope(self):
        if self._entered:
            raise ValueError('Publication verification scopes cannot be reused or nested')
        self._entered = True
        self._active, self._owner = True, current_thread()
        try:
            self._require()
            self._input = self._image(self._callbacks[1]())
            self._require()
            yield self
            self._check_images()
        finally:
            self._active = False
            self._owner = None
            self._input = self._output = self._result = None
            self._has_result = False

    def _check_images(self):
        self._require()
        if self._input != self._image(self._callbacks[1]()):
            raise ValueError('Publication source input changed')
        if self._has_result and self._output != self._image(self._callbacks[2](self._result)):
            raise ValueError('Publication verified result changed')
        self._require()

    def verify(self):
        self._check_images()
        if self._has_result:
            return self._result
        result = self._callbacks[0]()
        self._check_images()
        image = self._image(self._callbacks[2](result))
        self._require()
        self._result, self._output, self._has_result = result, image, True
        self._check_images()
        return result
