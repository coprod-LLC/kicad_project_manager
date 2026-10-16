"""Synthetic projects only; never copy private template/project data into tests."""
from pathlib import Path

CONFIG_TEXT = """# Keep this comment and KiBot's preprocessing syntax.
kibot:
  version: 1
outputs:
  - name: sample
    type: report
    options:
      output: '@BOARD_NAME@.txt'
...
definitions:
  # Project metadata
  PROJECT_NAME: Project Name  # display title
  BOARD_NAME: Board Name
  COMPANY: Example Company
  DESIGNER: Author
  GIT_URL: 'https://github.com/nguyen-v/KDT_Hierarchical_KiBot'
  LOGO: 'Logos/example.png'
  UNRELATED: '@KEEP_THIS@'
"""
WORKFLOW_TEXT = """name: Example CI
on:
  workflow_dispatch:
  push:
    branches: [dev]
env:
  kibot_config: kibot_yaml/kibot_main.yaml
  kibot_variant: DRAFT  # existing comment
  kicad_version: 9
jobs:
  example:
    runs-on: ubuntu-latest
    steps:
      - run: echo '${{ github.ref }}'
"""


def sample_project(root: Path, workflow: bool = True) -> Path:
    (root / 'kibot_yaml').mkdir(parents=True, exist_ok=True)
    (root / 'kibot_yaml/kibot_main.yaml').write_text(CONFIG_TEXT, encoding='utf-8')
    (root / 'sample.kicad_pro').write_text('{}\n', encoding='utf-8')
    (root / 'sample.kicad_pcb').write_text('(kicad_pcb)\n', encoding='utf-8')
    if workflow:
        (root / '.github/workflows').mkdir(parents=True)
        (root / '.github/workflows/ci.yaml').write_text(WORKFLOW_TEXT, encoding='utf-8')
    return root
