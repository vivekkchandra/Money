# AI Hedge Fund dependency compatibility

This change repairs the dependency metadata of the existing AI Hedge Fund
runtime. It does not change research admission, choose another upstream revision,
change investor-agent logic, or waive a security advisory.

## Pinned application source

- Repository: `https://github.com/virattt/ai-hedge-fund.git`
- Commit: `fc1bf250ead209ae5f02c39c3d0062c4bb554505`
- Distribution: `aihf`, upstream version `2.2.0`
- Isolated environment: `.venv-ai-hedge-fund`
- Python: `3.12.14`
- Expected Money source fingerprint:
  `4323005f036350ed3bb3b959a10215e4d5c7ecbc26d179751c15d8c4507a77e6`

The upstream source archive and its application files remain unchanged. A Money
compatibility patch must change only the declared dependency metadata in the
build copy; its identity must be recorded separately from the upstream source
identity. An updated dependency wheel is not a new upstream application commit.

## Observed security conflict

The saved native audit identified four affected installed packages. Repeated
aliases for the same finding are shown once here; the original audit remains
available as evidence.

| Package | Installed version | Fixed minimum | Advisory |
| --- | --- | --- | --- |
| `langchain-anthropic` | `0.3.5` | `1.4.6` | [CVE-2026-55443 / GHSA-gr75-jv2w-4656](https://github.com/advisories/GHSA-gr75-jv2w-4656) |
| `langchain-core` | `0.3.86` | `1.2.22` | [CVE-2026-34070 / GHSA-qh6h-p6c9-ff54](https://github.com/advisories/GHSA-qh6h-p6c9-ff54) |
| `langchain-core` | `0.3.86` | `1.2.11` | [CVE-2026-26013 / GHSA-2g6r-c272-w58r](https://github.com/advisories/GHSA-2g6r-c272-w58r) |
| `langchain-openai` | `0.3.35` | `1.1.14` | [CVE-2026-41488 / GHSA-r7w7-9xr2-qq2r](https://github.com/advisories/GHSA-r7w7-9xr2-qq2r) |
| `python-dotenv` | `1.0.0` | `1.2.2` | [CVE-2026-28684 / GHSA-mf9w-mj56-hr94](https://github.com/advisories/GHSA-mf9w-mj56-hr94) |

The OpenAI fix also requires `langchain-core>=1.2.31`. These are advisory fix
floors, not a substitute for auditing the complete resulting installed closure.

AIHF declares `langchain-anthropic==0.3.5` and `python-dotenv==1.0.0` directly.
No newer transitive selection can satisfy those equalities and the fixes.
Further, its old OpenAI, DeepSeek and xAI dependency ranges require the old
LangChain dependency family:

| Upstream declaration | Effective constraint | Why it prevents remediation |
| --- | --- | --- |
| `langchain-anthropic = "0.3.5"` | `==0.3.5` | Direct vulnerable pin; installed integration requires core `>=0.3.33,<0.4.0`. |
| `langchain-openai = "^0.3.5"` | `>=0.3.5,<0.4.0` | Excludes fixed OpenAI; installed integration requires core `<1.0.0`. |
| `langchain-deepseek = "^0.1.2"` | `>=0.1.2,<0.2.0` | Installed `0.1.4` requires OpenAI and core `<1.0.0`. |
| `langchain-xai = "^0.2.5"` | `>=0.2.5,<0.3.0` | Installed `0.2.5` requires OpenAI `<0.4` and core `<1.0.0`. |
| `python-dotenv = "1.0.0"` | `==1.0.0` | Excludes the fixed dotenv version. |

Installing fixed versions with a resolver override while leaving these wheel
requirements unchanged would not establish a valid closure: `uv pip check`
would report the incompatible installed requirements. The remedy must produce a
wheel whose explicit, recorded metadata matches the dependencies being resolved.
Do not use `--no-deps`, ignored advisories, or a modified audit result as a fix.

## Minimal compatibility boundary

The checked-in compatibility definition is
[`data/configuration/native-runtimes/aihf-compatibility.json`](../data/configuration/native-runtimes/aihf-compatibility.json).
It permits exactly these five metadata replacements; the generated runtime
lock then pins the selected package versions and hashes:

| Dependency | Money compatibility declaration |
| --- | --- |
| `langchain-anthropic` | `>=1.4.6,<2` |
| `langchain-openai` | `>=1.1.14,<2` |
| `langchain-deepseek` | `>=1,<2` |
| `langchain-xai` | `>=1,<2` |
| `python-dotenv` | `>=1.2.2,<2` |

Only those five declarations change. In particular,
`langchain-google-genai = "^2.0.11"` remains unchanged.
Its installed `2.1.12` metadata permits core `>=0.3.75` without an upper bound;
a read-only diagnostic successfully imported it against core `1.6.3`. That
diagnostic is not installed-environment qualification.

The upstream [DeepSeek `1.1.0` metadata](https://raw.githubusercontent.com/langchain-ai/langchain/master/libs/partners/deepseek/pyproject.toml)
requires core `>=1.4.7,<2` and OpenAI `>=1.1,<2`. The upstream
[xAI `1.3.0` metadata](https://raw.githubusercontent.com/langchain-ai/langchain/master/libs/partners/xai/pyproject.toml)
requires core `>=1.5,<2` and OpenAI `>=1.1.7,<2`. These support the newer secure
dependency family without changing AIHF investor application code.

## Reachability is not an audit exemption

Money injects its shared `InferenceSession` into the upstream Buffett and Lynch
agents. Their existing `LLMAgent.predict()` lifecycle remains in use. The
upstream `make_llm()` provider factory imports LangChain integrations lazily and
is not called when the LLM protocol implementation is supplied explicitly.

A read-only import of the actual Money adapter and the two native investor
modules loaded none of `langchain_anthropic`, `langchain_core`,
`langchain_openai`, or `dotenv`. The Money AIHF path does not intentionally call
the affected filesystem-search, legacy prompt-loading, image-token-counting, or
dotenv-writing functions. This limits the observed reachability; it does not
make an installed vulnerable package acceptable. The complete isolated
environment must pass the unsuppressed audit.

## Required acceptance evidence

The unmodified source and original lock remain available. Compatibility build
outputs are kept under a separate `compatibility/<definition_sha>` directory,
so an original failed audit is not rewritten into a patched-environment PASS.

The runtime must record the upstream revision, original metadata, compatibility
patch identity, resulting wheel and lock hashes, actual inventory, installed
source fingerprint and security-audit artifacts. Acceptance requires all of:

1. Normal dependency resolution and hash-locked installation succeed.
2. `uv pip check` succeeds against the installed environment.
3. Native AIHF investor modules and Money's adapter import successfully.
4. The pinned application source fingerprint is unchanged.
5. Compatibility and existing AIHF behavior tests pass.
6. Every installed package is covered by the unsuppressed security audit, with
   no unresolved finding.

Until those checks complete on the actual patched environment, this document
does not assert a secure closure or audit PASS. The current authoritative
result is `outputs/native-environments/ai_hedge_fund.json` beneath the selected
qualification root. The normal research command resumes setup automatically;
independent research and mandatory LEAN remain gated on their real prerequisites.

## Observed sandbox validation — 2026-09-18

The unchanged upstream wheel plus advisory fix floors produced a genuine
unsatisfiable resolution: `python-dotenv==1.0.0` conflicts with `>=1.2.2`.
The metadata-only compatibility wheel then built successfully with SHA256
`95d5a9fc63b70639ccf28712ce799f6f9e0ce047ec0011951611bd77f81c20fa`.
Its application fingerprint remains the upstream value above. The patch bytes
have SHA256 `9c45a154a0975e84b03382053991804ca72cfae73849fc6ced8abcb35c5070a8`.

Runtime resolution could not finish because the sandbox cannot resolve the
package index. A real offline retry specifically reported that
`langchain-deepseek>=1` needs an uncached registry download. No resulting secure
runtime lock or upgraded installed inventory is claimed. The old environment
and its vulnerability evidence are preserved. Machine-readable resolver receipts
are `outputs/native-environments/aihf-transitive-resolution.json` and
`outputs/native-environments/aihf-compatibility-resolution.json`.

An unchanged environment is reused only after its actual file bytes, lock and
compatibility identity verify. Imports, installed metadata, dependency consistency
and the security audit still run. Money can bootstrap the exact cached audit
tool without resolving that tool again; this does not put the advisory query in
offline mode or bypass missing advisory coverage.
