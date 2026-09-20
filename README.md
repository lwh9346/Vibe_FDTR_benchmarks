# Vibe-FDTR-Bench

[![arXiv](https://img.shields.io/badge/arXiv-2607.28200-b31b1b.svg)](https://arxiv.org/abs/2607.28200)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Benchmark suite and evaluation runner for the paper
[**Vibe-FDTR: An agent-oriented framework for reproducible frequency-domain
thermoreflectance data analysis**](https://arxiv.org/abs/2607.28200)
(Fuwei Yang, Weiheng Li, Bai Song).

It runs an [opencode](https://opencode.ai) agent on FDTR (frequency-domain
thermoreflectance) analysis tasks inside isolated Docker containers and
automatically grades the outputs against calibrated ground truth.

## Benchmark overview

The benchmark contains two scored task levels — **L1**: seven single-step tasks
on synthetic data, and **L2**: nine multi-step tasks on real Au/graphite
measurements — plus twelve underspecified **expert-mode (E)** tasks evaluated
qualitatively.

Each task is executed under three configurations, which correspond to the
ablation variants in the paper:

| Tier in this repo | Name in the paper | What the agent gets |
|-------------------|-------------------|---------------------|
| `full` | **Vibe-FDTR** | Full FDTR package + agent skills + docs |
| `code` | **Code-agent** | FDTR package source only, skills ablated |
| `minimal` | **Agent-only** | numpy/scipy/matplotlib only, no FDTR package |

The FDTR toolkit under test lives in the [`Vibe_FDTR/`](Vibe_FDTR/) submodule
([github.com/yfwsunny/Vibe_FDTR](https://github.com/yfwsunny/Vibe_FDTR)).

## Usage

See [AGENTS.md](AGENTS.md) for setup, configuration, and run instructions.

## Citation

```bibtex
@article{yang2026vibefdtr,
  title   = {Vibe-FDTR: An agent-oriented framework for reproducible
             frequency-domain thermoreflectance data analysis},
  author  = {Yang, Fuwei and Li, Weiheng and Song, Bai},
  journal = {arXiv preprint arXiv:2607.28200},
  year    = {2026}
}
```

## License

[MIT](LICENSE)
