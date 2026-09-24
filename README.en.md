# LarkDeck

> **Replies are typed out live in Feishu, with reasoning and tool calls kept in the card's bottom panel — expand it anytime.**
> LarkDeck is a [Hermes Agent](https://github.com/NousResearch/hermes-agent) plugin built on Feishu CardKit 2.0: it never patches Hermes source, and the answer, the process, and clarify choices all live in one card.

[![version](https://img.shields.io/badge/version-0.7.9-blue.svg)](https://github.com/zayn-0101/LarkDeck/releases)
[![AH (Hermes Agent) 0.21.x](https://img.shields.io/badge/AH-0.21.x-blueviolet.svg)](https://github.com/NousResearch/hermes-agent)
[![license](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

[中文](README.md) | [English](README.en.md)

## Highlights

- **One card per turn** — the first frame creates the card; the answer types out in place.
- **Panel** — reasoning and tool calls stay in a collapsible panel at the bottom, grouped by round.
- **Clarify cards** — answer with a dropdown, multi-select, text input, or buttons.
- **Status and usage** — green / red / yellow border for done / failed / stopped; the footer shows elapsed time, model and context usage.
- **Bilingual UI** — card chrome follows the Feishu client language; model output is never translated.
- **Safe fallback** — if a card step fails, the reply falls back to plain text or edit; messages are never lost.

## Quick start

Prerequisites: Hermes Agent 0.21.x running, with Feishu / Lark app credentials (see the [installation guide](INSTALL.md)).

```bash
git clone https://github.com/zayn-0101/LarkDeck.git
cd larkdeck
./install.sh          # add --copy for NAS / containers
```

Enable the plugin in `~/.hermes/config.yaml`:

```yaml
plugins:
  enabled:
    - larkdeck
```

Restart the gateway:

```bash
hermes gateway restart
```

Verify: send `/larkdeck status` to the bot in Feishu. A self-check card means the plugin has taken over. The gateway log shows a `[larkdeck]` startup self-check line; on failure it logs `ERROR` and leaves the official adapter working, so Feishu never breaks.

Upgrade: `git pull && hermes gateway restart` for a symlink install. For a copy install, `git pull`, move the old directory aside, re-run `./install.sh --copy`, then restart — the script never overwrites an existing target; see the [installation guide](INSTALL.md). The gateway must be restarted; modules are not hot-reloaded. Uninstall: remove `larkdeck` from `plugins.enabled`, then delete `~/.hermes/plugins/larkdeck/`.

## Features

| Capability | What you get |
|---|---|
| Streaming card | One main card per turn; text appears character by character, tool progress stays in the same card |
| Process panel | Reasoning and tool steps in a collapsible bottom panel, split by round with timing |
| Tool details | Tool name, argument preview, elapsed time and status per step; obvious credentials are masked |
| Clarify cards | Answer directly on the card with dropdown, multi-select, input or buttons |
| Turn status | Green / red / yellow border on done / failed / stopped; footer shows elapsed time, model and context usage |
| Long answers | Split into follow-up cards automatically, without replaying earlier text |
| Bilingual UI | Card chrome follows the client language; model output is not translated |
| Safe fallback | Any failure falls back to official plain text / edit; messages and content are never lost |

## Configuration

Minimal `~/.hermes/config.yaml`:

```yaml
plugins:
  stream_reasoning_deltas: true   # needed to see reasoning text; off by default in Hermes
  entries:
    larkdeck:
      settings:
        cards: true
        show_reasoning: auto      # follows Hermes /reasoning on|off
        streaming_print_ms: 15    # typing speed; 0 disables
        unified_panel: true
```

`LARKDECK_<KEY>` environment variables override config values (e.g. `LARKDECK_CARDS=0`); precedence is environment > `config.yaml` > default. See [Configuration](docs/guide/configuration.md) for every key, default and example; `/larkdeck config` shows effective values and `/larkdeck config reload` re-reads them.

## Commands

| Command | Purpose |
|---|---|
| `/larkdeck status` | Version, active transport, hooks, card writes and error records |
| `/larkdeck config` | Read-only view of effective settings and their source |
| `/larkdeck config reload` | Re-read settings from Hermes; aborts as a whole if any key fails |
| `/larkdeck help` | Usage |
| `/reasoning on\|off` | Hermes command: toggle reasoning text; `show_reasoning: auto` follows within ~1s |

See [Commands](docs/guide/commands.md).

## Compatibility and limits

- **Environment**: Hermes Agent 0.21.x (verified on 0.21.1 / 0.21.4), with the official `feishu` platform available in the same process; cannot coexist with plugins that also take over the same `feishu` platform or patch Hermes source.
- **Fallback and client differences**: cards are an enhancement. Any card, streaming or interaction failure falls back to official plain text / edit, so no message is lost; styling or animation may be missing for that reply. Clients that do not support card 2.0 may render fewer components or simpler forms; card chrome follows the client language, while model output and some markdown labels are language-fixed. See [Card capabilities](docs/guide/card-capabilities.md).
- **Reasoning text**: requires Hermes `plugins.stream_reasoning_deltas` (off by default); without it the panel shows tool steps only, and `/larkdeck status` says why.
- **Command timing**: in the Feishu gateway, commands sent while a reply is streaming are queued until the turn ends; the CLI / TUI runs them immediately.
- **Scope of counters**: footer metrics and `/larkdeck status` records are process-wide, not per conversation; after a long answer splits cards, `/stop` recolors only the newest card.

## Documentation

| Document | Contents |
|---|---|
| [Installation](INSTALL.md) | Install, upgrade, uninstall, rollback |
| [Quickstart](docs/guide/quickstart.md) | First card in five minutes |
| [Configuration](docs/guide/configuration.md) | All settings, defaults and examples |
| [Commands](docs/guide/commands.md) | Command reference |
| [Card capabilities](docs/guide/card-capabilities.md) | Supported and unsupported card features |
| [Troubleshooting](docs/guide/troubleshooting.md) | Symptom → cause → fix |
| [Architecture](docs/development/architecture.md) | Module layers, hooks, transport |
| [Release notes](docs/releases/README.md) | Full notes per release |
| [Changelog](CHANGELOG.md) | User-visible changes |
| [Contributing](CONTRIBUTING.md) | Development, tests, release flow |
| [License](LICENSE) | MIT license |

## Acknowledgements

LarkDeck's design and implementation were inspired by several excellent Feishu / Lark card projects in the community. Our thanks to the following projects and their authors:

- [Cheerwhy/hermes-lark-streaming](https://github.com/Cheerwhy/hermes-lark-streaming)
- [Aowen-Nowor/hermes-lark-streaming](https://github.com/Aowen-Nowor/hermes-lark-streaming)
- [monkey2jack/aiduPOP](https://github.com/monkey2jack/aiduPOP)
- [techysy/hermes-fry-cards](https://github.com/techysy/hermes-fry-cards)
- [baileyh8/hermes-feishu-streaming-card](https://github.com/baileyh8/hermes-feishu-streaming-card)

Thanks also to [Hermes Agent](https://github.com/NousResearch/hermes-agent) and the [Feishu Open Platform](https://open.feishu.cn/) for the public capabilities they provide.

LarkDeck is an independent implementation and is not affiliated with the projects above; all project names and work belong to their respective authors.

## Contributing

Issues and pull requests are welcome; please read the [contributing guide](CONTRIBUTING.md) first.

## License

MIT — see [LICENSE](LICENSE).
