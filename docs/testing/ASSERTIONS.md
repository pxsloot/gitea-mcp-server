---
audience: developer
type: reference
covers: Assertion best practices — general principles, examples, validation testing patterns
---

# Assertion Best Practices

## General Principles

1. **Be specific**: Use exact equality checks or precise assertions
2. **Test one behavior per test**: Each test should validate one specific outcome
3. **Use appropriate assertions**:
   - `assert value == expected` for equality
   - `assert in` for membership
   - `pytest.raises()` for exceptions
   - `assert isinstance(obj, Class)` for type checking

## Examples

```python
# Good
assert result["id"] == 42
assert len(items) == 3
assert "admin" in tool.tags

# Better — with descriptive messages
assert result["id"] == 42, f"Expected ID 42, got {result['id']}"
assert len(items) == 3, f"Expected 3 items, got {len(items)}"
```

## Validation Testing

When testing validation logic (e.g., in `validation.py`):

- **Test each validator** with both valid and invalid inputs. Use `pytest.mark.parametrize` to cover many cases.
- **Test schema augmentation**: verify that the tool's JSON schema gets the expected constraints (`minLength`, `maxLength`, `pattern`, `enum`, etc.).
- **Test runtime wrapper**: wrap a mock tool, call with invalid arguments, and assert `ValidationError` is raised *before* the tool's `run` method executes. Use `AsyncMock` for the original run.
- **Coverage**: Aim for >95% coverage of validation modules.

```python
@pytest.mark.parametrize("owner,repo,should_pass", [
    ("valid-owner", "valid-repo", True),
    ("", "repo", False),
    ("owner", "", False),
    ("a" * 256, "repo", False),
    ("owner", "../repo", False),
])
def test_validate_owner_repo(owner, repo, should_pass):
    if should_pass:
        validate_owner_repo(owner, repo)  # should not raise
    else:
        with pytest.raises(ValidationError):
            validate_owner_repo(owner, repo)
```
