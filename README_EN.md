# travel-photo-styling

Turn your travel photos into polished design posters: sticker collection pages, watercolor ticket stubs, enamel badges, acrylic scene maps, geometric deconstructions, typography posters… 9 battle-tested style templates + a batch-generation CLI that works with **any** image generation API.

[中文文档](README.md)

**Zero dependencies** (Python 3.8+ standard library) · **Templates are portable** (paste them into ChatGPT / any image tool) · **API-extensible** (Imagifly and OpenAI-compatible APIs built in; anything else via pure config)

![tests](https://github.com/shaozheng0503/travel-photo-styling/actions/workflows/tests.yml/badge.svg)

## Gallery

All images below were generated with templates from this repo (same photo → multiple style translations; top = original, bottom = AI reconstruction):

| Four-layer translation | Memory stickers | Watercolor ticket |
|:---:|:---:|:---:|
| ![four-layer](docs/images/demo-four-layer-gate.jpg) | ![sticker](docs/images/demo-sticker.jpg) | ![ticket](docs/images/demo-ticket.jpg) |

| Enamel badge | Acrylic SceneMap | Geometric |
|:---:|:---:|:---:|
| ![badge](docs/images/demo-badge.jpg) | ![acrylic](docs/images/demo-acrylic.jpg) | ![geometric](docs/images/demo-geometric.jpg) |

| Typography | Four-layer · night | Acrylic · night |
|:---:|:---:|:---:|
| ![typography](docs/images/demo-typography.jpg) | ![night](docs/images/demo-four-layer-night.jpg) | ![acrylic night](docs/images/demo-acrylic2.jpg) |

The full "photo type → style" compatibility matrix is in [prompts/风格速查表.md](prompts/风格速查表.md) (Chinese).

## How it works

```
your photo ──┐
             ├──> prompt template (prompts/) ──> queue (prompts.txt) ──> Provider ──> any image API
defaults ────┘                                                     ↑
                                        imagifly / openai_compat / custom (config-only)
```

1. **Pick a template** from `prompts/`
2. **Enqueue**: `python main.py --render i2i/03a_memory_sticker.txt --img photo.jpg --add`
3. **Precheck**: `--validate / --estimate / --dry-run` (no API calls, no cost)
4. **Run**: `python main.py` — outputs land in `images/`, successes move to `done.txt`, failures stay in `failed.jsonl` for retry

## Install

```bash
git clone https://github.com/shaozheng0503/travel-photo-styling.git
cd travel-photo-styling
python main.py --list-providers   # sanity check
```

No `pip install` required.

## Quick start

### 1. Configure an image API (pick one)

**A. Imagifly (default, works out of the box)**

```bash
# Log in to https://imagifly.net → F12 → Network → copy the full Cookie header of any request
cp config/cookie.txt.example config/cookie.txt   # paste it in
python main.py --check                           # verify
```

**B. OpenAI-compatible API (OpenAI / DashScope / SiliconFlow / one-api relays)**

Edit the `openai_compat` section in `config/providers.json` (or set `OPENAI_API_KEY`):

```json
"openai_compat": {
  "api_key": "sk-xxx",
  "base_url": "https://api.openai.com/v1",
  "models": {"gpt-image-1": {"ref": 1, "cost": null}}
}
```

**C. Any other API (config only, no code)**

Copy the `myapi` section in `config/providers.json`. Three response modes:
- `sync_url`: POST returns the image URL directly (most common)
- `sync_b64`: POST returns base64
- `poll_task`: POST returns a task ID, then poll another endpoint

URLs, headers and bodies all support `{prompt} {model} {ratio} {api_key} {id}` placeholder templates.

### 2. Generate your first image

```bash
# Image-to-image: photo → sticker collection page
python main.py --render i2i/03a_记忆贴纸_单层_I2I.txt --img your-photo.jpg --add
python main.py --validate
python main.py

# Text-to-image: fill placeholders with --var
python main.py --render t2i/01_手绘旅行海报_T2I.txt --var COUNTRY=Japan --add
python main.py
```

T2I placeholders: template 01 uses `[COUNTRY]`; template 02 uses `[CITY]` `[LANDMARK]` `[LOCAL_BUILDING]`. Missing ones trigger a warning at render time.

### 3. Batch mode

Each line in `prompts.txt` is one task; inline params follow `|`:

```
<rendered template prompt> | model=gpt-image-2 | 3:4 | img=/path/to/photo1.jpg
<rendered template prompt> | model=nano-banana-2 | 3:4 | img=/path/to/photo2.jpg
a cyberpunk cat | model=Wai-SDXL | 1:1 | x4
```

| Inline param | Meaning |
|---|---|
| `model=` | Model name (see `--list-models`) |
| `provider=` | Image service (imagifly / openai_compat / custom section) |
| `3:4` etc. | Aspect ratio |
| `x2`–`x4` | Number of images per task |
| `img=` | Reference image path (comma-separated for multiple) |
| `style=` / `neg=` / `steps=` / `tier=` | Style / negative prompt / steps / resolution tier |

## CLI reference

```bash
python main.py --list-providers                          # available services
python main.py --list-models                             # models of current provider
python main.py --check                                   # config / login check
python main.py --render <template> --var K=V --add       # template → queue
python tools/build_queue.py <plan.json> --add            # batch plan → queue
python main.py --validate                                # validate queue (no API)
python main.py --estimate                                # cost estimate (no API)
python main.py --dry-run                                 # parse preview (no API)
python main.py                                           # run the whole queue
python main.py 3                                         # run first 3 only
python main.py --confirm                                 # allow high-cost tasks
python main.py --retry-failed                            # re-enqueue failures
python main.py --summary                                 # result summary
python tests/test_offline.py                             # offline tests (66 cases, zero cost)
```

### Batch plan (dozens of jobs at once)

Instead of repeating `--render`, write one JSON plan (templates × photos × params):

```bash
cp examples/batch-plan.example.json my-plan.json
python tools/build_queue.py my-plan.json --check         # validate only
python tools/build_queue.py my-plan.json --add           # expand into prompts.txt
```

`defaults` holds shared params, `images` aliases your photos, each `tasks` entry picks a
`template` + `image` + `vars` and may override any inline param.

## Plugging in your own API

### Option 1: config only (recommended)

Add a section to `config/providers.json`:

```json
"my-service": {
  "type": "generic",
  "api_key": "sk-xxx",
  "models": {"my-model": {"ref": 1, "cost": 1}},
  "templates": {
    "submit": {
      "method": "POST",
      "url": "https://api.example.com/generate",
      "headers": {"Authorization": "Bearer {api_key}"},
      "body_json": {"model": "{model}", "prompt": "{prompt}", "size": "{ratio}"}
    },
    "poll": {
      "method": "GET",
      "url": "https://api.example.com/tasks/{id}",
      "headers": {"Authorization": "Bearer {api_key}"}
    }
  },
  "response": {"mode": "sync_url", "url_key": "data.0.url"}
}
```

Then use `provider=my-service` in your queue, or set `"provider": "my-service"` in `config/config.json` to make it the default.

Placeholders: `{prompt} {model} {ratio} {batch} {style} {images_json} {api_key} {id} {nonce} {timestamp}`

### Option 2: write a Provider class (~30 lines)

```python
# providers/my_provider.py
from providers.base import ImageProvider

class MyProvider(ImageProvider):
    name = "my_provider"
    def submit(self, job, model, ratio, batch, style): ...
    def poll(self, handle, job=None, timeout=600, interval=2): ...
    def download(self, asset, dest_dir, prompt, idx, kind="image"): ...

def register(registry):
    registry["my_provider"] = MyProvider
```

Add `"my_provider"` to `BUILTIN_PROVIDERS` in `providers/__init__.py`. See `providers/base.py` for the full contract.

## About the prompt templates

All 9 templates in `prompts/` went through multiple rounds of real-generation iteration. Every "anti-failure clause" (no-cropping, sticker deduplication, verbatim place names, seasonal dates) exists because of an actual failure. Usage guide: [prompts/README.md](prompts/README.md) (Chinese).

The templates are plain text — you can paste them into any image generation product alongside your photo; they don't depend on this CLI.

## FAQ

**Q: Do I have to use Imagifly?**
No. It's just the default. OpenAI-compatible APIs need one config change; anything else is a config-only `myapi`-style section. The templates themselves are platform-agnostic.

**Q: Can it accidentally spend money?**
`--validate / --estimate / --dry-run` are all zero-cost. Any task costing ≥5 units (platform-dependent) is blocked unless you pass `--confirm`.

**Q: Will my cookie / API key leak?**
No. `config/cookie.txt` and `*.local.json` are gitignored; never put real keys in `providers.json`.

**Q: What if a run is interrupted?**
Each success is atomically moved to `done.txt` — just re-run to resume. Failures land in `failed.jsonl`; `--retry-failed` re-enqueues them.

## License

- **Code & docs**: [MIT](LICENSE)
- **Prompt templates** (`prompts/`): [CC BY 4.0](LICENSE-PROMPTS.md) — commercial use and
  modification allowed, just keep attribution

Contributing? See [CONTRIBUTING.md](CONTRIBUTING.md). Driving this from an AI agent?
See [SKILL.md](SKILL.md).

Generated results depend on the model and platform you use. This repo provides templates and tooling only; no guarantee of any third-party platform's availability.
