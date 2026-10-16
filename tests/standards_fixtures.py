"""Small synthetic standards; no company presets or project data."""
import json
from pathlib import Path

STACKUP = '''(stackup
  (layer "F.Cu" (type "copper") (thickness 0.035))
  (layer "dielectric 1" (type "core") (thickness 1.53) (material "FR4"))
  (layer "B.Cu" (type "copper") (thickness 0.035))
  (copper_finish "ENIG"))
'''
LAYERS = '(layers (0 "F.Cu" signal) (2 "B.Cu" signal))\n'
RULES = '''(version 1)
(rule "Test domain"
  (condition "A.hasNetclass('SIGNAL') && A.intersectsArea('Protected') && B.NetName == 'FRAME'")
  (constraint clearance (min 0.2mm)))
'''


def catalog_folder(root: Path) -> Path:
    root.mkdir(parents=True)
    files = {'stackup.sexpr': STACKUP, 'layers.sexpr': LAYERS, 'rules.kicad_dru': RULES,
             'constraints.json': json.dumps({'board': {'design_settings': {'rules': {'min_clearance': 0.2}}}}),
             'classes.json': json.dumps({'net_settings': {'classes': [{'name': 'SIGNAL', 'clearance': 0.2}]}})}
    for name, text in files.items():
        (root / name).write_text(text, encoding='utf-8')
    data = {'schema_version': 1, 'kicad_major': 9, 'name': 'Example standards', 'status': 'draft',
            'stackups': [{'id': 'two_layer', 'name': 'Two-layer example', 'copper_layer_count': 2,
                         'board_thickness_mm': 1.6, 'copper': [{'layer': 'F.Cu', 'thickness_mm': 0.035},
                                                           {'layer': 'B.Cu', 'thickness_mm': 0.035}],
                         'stackup_file': 'stackup.sexpr', 'copper_layers_file': 'layers.sexpr'}],
            'rules': [{'id': 'example_rules', 'name': 'Example rules', 'custom_rules_file': 'rules.kicad_dru',
                       'constraints_file': 'constraints.json', 'netclass_definitions_file': 'classes.json'}]}
    (root / 'catalog.json').write_text(json.dumps(data), encoding='utf-8')
    return root


def saved_board(root: Path) -> tuple[Path, Path]:
    root.mkdir(parents=True, exist_ok=True)
    project, board = root / 'sample.kicad_pro', root / 'sample.kicad_pcb'
    project.write_text(json.dumps({'board': {'design_settings': {'rules': {'min_clearance': 0.1, 'min_track_width': 0.1}}},
                                  'net_settings': {'classes': [{'name': 'Default'}]}, 'unrelated': 'keep'}))
    board.write_text('(kicad_pcb (version 20241229) (generator "pcbnew") (general (thickness 1.6))\n'
                     + LAYERS + '(setup ' + STACKUP + ')\n(net 1 "FRAME")\n'
                     '(zone (name "Protected") (keepout (tracks not_allowed)))\n'
                     '(footprint "a (# not a comment)" (property "Reference" "R1")))\n')
    board.with_suffix('.kicad_dru').write_text('(version 1)\n# existing exception stays untouched\n')
    return project, board
