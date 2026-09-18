# NiceGUI bug report (draft): `Storage.clear()` fails on Windows, cascading into unrelated test failures

Not filed. Written up so it can be submitted to
<https://github.com/zauberzeug/nicegui/issues> when convenient. Everything below is
phrased for that audience, so it does not assume any knowledge of this project.

---

## Summary

On Windows, `Storage.clear()` raises `OSError: [WinError 145] The directory is not empty`
whenever an atomic storage write is still in flight. Under pytest this happens inside the
`user` fixture's teardown, which leaves NiceGUI's globals half-reset — so **every
subsequent test in the session gets a 404 for pages that are correctly registered**.

The reported symptom is therefore nothing like the cause. It presents as "my routes
disappeared halfway through the suite".

## Environment

| | |
|---|---|
| nicegui | 3.16.0 |
| Python | 3.14.2 |
| OS | Windows 11 |
| pytest | 8.4.2 |
| pytest-asyncio | 1.4.0 (`asyncio_mode = auto`) |

Plugin registered as `nicegui.testing.user_plugin` (not the full `plugin`, to avoid the
Selenium dependency of the `screen` fixtures).

## Reproducer

Any page that writes to `app.storage.user` shortly before the test ends. A timer that
persists form state is the realistic case; the write just has to be recent enough that
its atomic-write temporary file is still open at teardown.

```python
# app_under_test.py
from nicegui import app, ui

@ui.page('/')
def index():
    ui.label('hello')

    def persist() -> None:
        app.storage.user['counter'] = app.storage.user.get('counter', 0) + 1

    ui.button('Save', on_click=persist)

ui.run(storage_secret='test-secret')
```

```ini
# pytest.ini
[pytest]
asyncio_mode = auto
main_file = app_under_test.py
```

```python
# test_repro.py
from nicegui.testing import User

async def test_a_writes_storage(user: User) -> None:
    await user.open('/')
    user.find('Save').click()
    await user.should_see('hello')

async def test_b_is_collateral_damage(user: User) -> None:
    await user.open('/')          # 404
    await user.should_see('hello')
```

`test_a` passes but **errors at teardown**; `test_b` then fails with
`AssertionError: Expected status code 200, got 404`.

Printing `Storage.path.glob('*')` at the end of `test_a` shows exactly one leftover:

```
['storage-user-87671f82-9725-414d-b7ef-38c5ee42aaab.json.tmp']
```

## Diagnosis

`nicegui/storage.py`, `Storage.clear()`:

```python
for filepath in self.path.glob('storage-*.json'):
    helpers.unlink_with_retry(filepath, missing_ok=True)
for tmp_path in self.path.glob('storage-*.json.tmp'):
    with contextlib.suppress(OSError):  # never wait: only an in-flight backup on this loop can hold it
        tmp_path.unlink()
if self.path.exists():
    self.path.rmdir()
```

Suppressing the `.tmp` unlink is deliberate, and the comment explains why: the method
must not block waiting for an in-flight backup. The problem is that `rmdir()` on the next
line is unconditional, and the two lines only agree with each other on POSIX:

- **POSIX** — unlinking a file another handle holds still removes the directory entry, so
  the suppressed error is rare and the directory genuinely ends up empty. `rmdir()`
  succeeds.
- **Windows** — `unlink` on a file held open raises `PermissionError` (a subclass of
  `OSError`, so it is suppressed), the directory entry survives, and `rmdir()` then fails.

So the suppression that is correct on POSIX becomes a guaranteed `rmdir` failure on
Windows.

The consequence is disproportionate because of where `clear()` is called from.
`nicegui/testing/general.py::nicegui_reset_globals` runs `app.reset()` during teardown,
*after* `Client.page_routes.clear()`. The exception aborts the rest of that teardown, so
the registered-route bookkeeping is never restored and the next test's `main_file` import
does not put the routes back.

I have verified the leftover file and read the source on Windows. The POSIX half of the
contrast is inferred from the code and the suppression comment rather than executed, so
the Linux behaviour should be confirmed before the issue text asserts it.

## Suggested fix

Make the directory removal as tolerant as the unlink above it:

```python
if self.path.exists():
    with contextlib.suppress(OSError):
        self.path.rmdir()
```

or, since the directory is a `mkdtemp` the module also registers with `atexit`:

```python
shutil.rmtree(self.path, ignore_errors=True)
```

Either keeps a leftover temporary file from aborting the reset. The `atexit` handler
already removes the tree at process exit, so nothing leaks.

A second, smaller point: even with the fix, a failure inside `nicegui_reset_globals`
leaves the framework in a half-reset state that silently breaks later tests. Wrapping the
body so the reset always completes would make any future failure there local to one test
instead of poisoning the session.

## Workaround

Patch the cleanup for the duration of the test session:

```python
@pytest.fixture(autouse=True)
def _tolerate_storage_cleanup(monkeypatch):
    original = Storage.clear

    def clear(self) -> None:
        try:
            original(self)
        except OSError:
            shutil.rmtree(self.path, ignore_errors=True)

    monkeypatch.setattr(Storage, 'clear', clear)
```

The fixture must be autouse and depend on nothing, so it is set up before `user` and
therefore torn down after it — otherwise the patch is gone by the time the failure
happens.
