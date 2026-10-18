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
    files = {'rules.kicad_dru': RULES,
             'constraints.json': json.dumps({'board': {'design_settings': {'rules': {'min_clearance': 0.2}}}}),
             'classes.json': json.dumps({'net_settings': {'classes': [{'name': 'SIGNAL', 'clearance': 0.2}]}})}
    for name, text in files.items():
        (root / name).write_text(text, encoding='utf-8')
    write_stackup(root / 'stackups/two_layer.json', 'two_layer', 'Two-layer example', STACKUP)
    data = {'schema_version': 2, 'kicad_major': 9, 'name': 'Example standards',
            'rules': [{'id': 'example_rules', 'name': 'Example rules', 'custom_rules_file': 'rules.kicad_dru',
                       'constraints_file': 'constraints.json', 'netclass_definitions_file': 'classes.json'}]}
    (root / 'catalog.json').write_text(json.dumps(data), encoding='utf-8')
    return root


def write_stackup(path: Path, pid: str, name: str, native: str, **metadata) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {'schema_version': 1, 'kicad_major': 9, 'id': pid, 'name': name,
            'status': 'draft', **metadata, 'stackup': native.splitlines()}
    path.write_text(json.dumps(data, indent=2), encoding='utf-8')
    return path


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


def detailed_catalog(root: Path) -> Path:
    """Synthetic six-layer construction with distinct dielectric plies and surfaces."""
    catalog_folder(root)
    layers = ['(layer "F.SilkS" (type "Top Silk Screen") (color "White") (material "Direct Printing"))',
              '(layer "F.Paste" (type "Top Solder Paste"))',
              '(layer "F.Mask" (type "Top Solder Mask") (color "Black") (thickness 0.02) (material "Resist") (epsilon_r 3.3) (loss_tangent 0))']
    names = ['F.Cu', 'In1.Cu', 'In2.Cu', 'In3.Cu', 'In4.Cu', 'B.Cu']
    gaps = [0.18, 0.3, None, 0.3, 0.18]
    for i, name in enumerate(names):
        layers.append(f'(layer "{name}" (type "copper") (thickness {0.035 if i in (0, 5) else 0.0175}))')
        if i == 2:
            layers.append('(layer "dielectric 3" (type "prepreg") (thickness 0.24 locked) '
                          '(material "Weave A") (epsilon_r 4.2) (loss_tangent 0.02) '
                          'addsublayer (thickness 0.2) (material "Weave B") (epsilon_r 3.8) (loss_tangent 0.018))')
        elif i < 5:
            layers.append(f'(layer "dielectric {i+1}" (type "{ "core" if i % 2 else "prepreg" }") '
                          f'(thickness {gaps[i]}) (material "FR4") (epsilon_r 4.3) (loss_tangent 0.02))')
    layers += ['(layer "B.Mask" (type "Bottom Solder Mask") (color "Black") (thickness 0.02) (material "Resist"))',
               '(layer "B.Paste" (type "Bottom Solder Paste"))',
               '(layer "B.SilkS" (type "Bottom Silk Screen") (color "White") (material "Direct Printing"))']
    native = '(stackup\n' + '\n'.join(layers) + '\n(copper_finish "ENIG") (dielectric_constraints yes))\n'
    write_stackup(root / 'stackups/example_fab/ex0616.json', 'example_six_layer', 'Example Fab EX0616', native,
                  manufacturer='Example Fab', manufacturer_stackup_id='EX0616',
                  nominal_thickness_mm=1.6, outer_copper_oz=1, inner_copper_oz=0.5)
    return root
