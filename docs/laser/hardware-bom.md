# Laser Hardware BOM Baseline

This project uses the user's physical laser engraver as the default software target. The hardware is built from the Bilibili Dayu DIY "Yixiu V1.0" procurement list, so generated jobs and default safety limits should assume this machine unless later device evidence proves otherwise.

## Source

- Source file: `B站【大鱼DIY】激光雕刻机采购清单_翼宿 V1.0.xlsx`
- Source package directory: `B站【大鱼DIY】激光雕刻机采购清单_翼宿 V1.01 全套资料`
- Video reference in the workbook: `https://www.bilibili.com/video/BV1PdFhzjEzL/`

Do not copy seller-specific runtime values, private serial ports, device IPs, Wi-Fi credentials, or tokens into this document.

## Machine Profile

| Field | Baseline |
|---|---|
| Machine | 大鱼 DIY 翼宿 V1.0 laser engraver |
| Work area | 100 mm x 100 mm |
| Controller board | G3 |
| Laser module | 24 V, 5 W, wired module |
| Power supply | 24 V, 6 A |
| Motion system | GT2 belt drive with MGN9H linear rails |
| Rails | 140 mm rail + MGN9H carriage, quantity 3 |
| Stepper motors | 42 stepper motor, 40 mm height, quantity 2 for Y axis |
| Cooling | 4028 fan, 24 V, 10000 RPM |
| Frame/parts | PETG printed parts, semi-transparent 3 mm acrylic, pulleys, idlers, 688ZZ bearings, 8 mm shaft, M3/M4 hardware |

## Software Implications

- Treat the default generated-job envelope as `100 mm x 100 mm`; generated image and calibration-grid G-code should reject dimensions that exceed this workspace.
- Scan existing `.gcode`/`.nc` files before sending. A direct file must not move outside `0..100 mm` on X/Y, including `G2`/`G3` arc extents, and must not exceed the configured `GRBL_LASER_S_MAX` power scale.
- Treat the laser as a `5 W` diode module. Use conservative defaults and material calibration rather than assuming higher-power laser behavior.
- Treat GRBL `S` power values as controller-scale values, not watts. The default software scale is `0..1000` unless `GRBL_LASER_S_MAX` is configured differently.
- Keep `send_file` as the full job path. It may scan an existing `.gcode`/`.nc` file, convert an image to G-code, set the job origin for converted files, and stream a whole file, but `confirmed=false` only returns a preview.
- Keep `send_command` for single manual GRBL commands only. Serial and network command helpers classify read-only, motion, laser, config-write, destructive, and unknown commands; state-changing commands require `confirmed=true`, but they are still not a substitute for a full engraving/cutting job.
- Confirmed serial and network file sends must run a read-only online probe before streaming: serial uses `?`; network complete-file sends are forced to Telnet and also use `?`.
- `check_laser_connection_tool` is safe for connection checks because it uses only read-only probes and does not return full device details unless `include_detail=true`.
- The workbook does not specify firmware, serial port, baud rate, microstepping, or GRBL settings. Verify those with read-only commands such as `$I`, `$$`, and `$G` before changing controller assumptions.

## Runtime Configuration Keys

These keys may override the hardware profile without changing code:

| Key | Default | Purpose |
|---|---:|---|
| `LASERGRBL_MACHINE_NAME` | `翼宿 V1.0` | Human-readable machine profile name |
| `LASERGRBL_WORK_AREA_WIDTH_MM` | `100` | Maximum generated job width in millimeters |
| `LASERGRBL_WORK_AREA_HEIGHT_MM` | `100` | Maximum generated job height in millimeters |
| `LASERGRBL_DEFAULT_IMAGE_FIT_BOX_WIDTH_MM` | `50` | Default image fit width when converting images without explicit size |
| `LASERGRBL_DEFAULT_IMAGE_FIT_BOX_HEIGHT_MM` | `50` | Default image fit height when converting images without explicit size |
| `LASERGRBL_SAFE_MARGIN_MM` | `5` | Default margin used to derive the safe image placement area |
| `LASERGRBL_LASER_OPTICAL_POWER_W` | `5` | Human-readable laser module optical power baseline |
| `GRBL_LASER_S_MAX` | `1000` | Maximum allowed GRBL `S` value for generated G-code |
| `LASER_ENGRAVING_MODE` | `raster` | Default engraving strategy when callers do not specify `engraving_mode` |
| `LASER_RASTER_OVERSCAN_MM` | `1.5` | Raster scanline overscan used by image-to-G-code helpers |
| `LASERGRBL_DEFAULT_IMAGE` | empty | Optional default image file for `send_file` fallback |
| `GRBL_DEFAULT_FILE` | empty | Optional default `.gcode`/`.nc` file for serial/network send fallback |
| `GRBL_DEFAULT_PORT` | empty | Optional preferred serial port; when empty the serial helper may auto-detect GRBL |
| `GRBL_BAUDRATE` | `115200` | Serial baud rate |
| `LASERGRBL_JOB_DIR` | Windows local engraving directory from code | Directory searched when a user provides only a plain file name for existing image/G-code sending |

Additional runtime keys used by the current MCP tools:

| Key | Default | Purpose |
|---|---:|---|
| `LASER_DEFAULT_CONNECTION_MODE` | `network` | Default routing for calibration, text-task sending, and semantic safe actions when the user does not say serial/network |
| `LASER_NETWORK_HOST` | empty | Default Grbl_ESP32 / ESP3D host; leave empty rather than guessing an IP |
| `LASER_NETWORK_HTTP_PORT` | `80` | HTTP `/command` port for read-only or single-command network control |
| `LASER_NETWORK_TELNET_PORT` | `23` | Telnet port for complete network G-code/file sends |
| `LASER_NETWORK_TIMEOUT` | `5` | Network command/probe timeout in seconds |
| `LASER_MATERIAL_PARAMS_FILE` | `.runtime/lasergrbl_materials.json` | Material parameter library override |
| `LASER_CALIBRATION_DIR` | `.runtime/lasergrbl_calibrations` | Calibration session and generated grid output directory |
| `LASER_MAX_PASSES` | `10` | Upper clamp used by feedback/regeneration and calibration parameter helpers |
| `LASER_TRAVEL_RATE` | `3000` | Default travel rate used by calibration helpers |
| `LASER_MIN_FEED_RATE` | `50` | Minimum feed-rate clamp used when adjusting text laser task parameters |
| `LASER_TEXT_TASKS_DIR` | `.runtime/lasergrbl_text_tasks` | Persisted text laser task directory |
| `LASER_PREPARED_GCODE_DIR` | `generated/` | Directory searched for prepared `.gcode`/`.nc` files in text-task lookup flows; configure your own path in `.env` |
| `TEXT_IMAGE_OUTPUT_DIR` | `.runtime/generated_images/` | Output directory for `generate_text_image_tool` when no output path is passed |
| `AI_LASER_INPUT_DIR` | `laser_inputs/` | Voice image/G-code fallback input directory |
| `AI_LASER_GCODE_OUTPUT_DIR` | `out` | `ai_laser_gcode_tool` output directory |
| `AI_LASER_GCODE_ASSETS_DIR` | `generated_assets` | Directory for text, URL, and image-search assets prepared by `ai_laser_gcode_tool` |
| `AI_LASER_GCODE_STATE_PATH` | `.runtime/ai_laser_gcode_state/state.json` | Recent material and image-search candidate state |
| `AI_LASER_CANDIDATE_TTL_SECONDS` | `1800` | Expiration time for saved image-search candidates |
| `AI_LASER_OPENAI_BASE_URL` | empty | Optional OpenAI-compatible endpoint for AI image analysis, not required for local generation |
| `AI_LASER_OPENAI_API_KEY` | empty | Optional AI image-analysis API key; never document the real value |
| `AI_LASER_OPENAI_MODEL` | empty | Optional AI image-analysis model name |

## BOM Summary

| Category | Item | Specification | Quantity | Workbook total |
|---|---|---|---:|---:|
| Electronics | Controller board | G3 | 1 | 96 |
| Electronics | Laser module | 24 V 5 W with wiring | 1 | 368 |
| Structure | PETG printed parts | Full Yixiu printed set, 3 walls, 40% infill | 1 | 30 |
| Motion | Linear rails | 140 mm rail + MGN9H carriage | 3 | 75.6 |
| Bundle | Yixiu accessory kit | See accessory sheet | 1 | 75 |
| Hardware | Screw/nut kit | See hardware sheet | 1 | 23 |
| Power | Power adapter | 24 V 6 A | 1 | 30 |
| Panel | Semi-transparent acrylic | Custom cut, 3 mm thickness | 1 | 18 |

Main sheet total: `715.6`.

## Accessory Sheet Highlights

| Item | Specification | Quantity |
|---|---|---:|
| 42 stepper motor | 40 mm height, 80 cm XH2.54 wire, Y axis | 2 |
| GT2 pulley | 20 teeth, 5 mm bore, 16 mm height, 6 mm belt | 2 |
| GT2 pulley | 20 teeth, 8 mm bore, 16 mm height, 6 mm belt | 1 |
| Double-slot pulley | 20 teeth, 8 mm bore | 1 |
| Idler | 16 teeth, 3 mm bore, toothed, 6 mm belt | 2 |
| Idler | 16 teeth, 3 mm bore, smooth, 6 mm belt | 2 |
| Idler | 20 teeth, 3 mm bore, toothed, 6 mm belt | 2 |
| GT2 belt | 6 mm width, 2 m | 1 |
| Closed-loop GT2 belt | GT2-122 mm | 1 |
| Fan | 4028, ball bearing, 24 V, 10000 RPM | 1 |
| Bearing | 688ZZ, 8 x 16 x 5 mm | 2 |
| Shaft | 8 mm diameter, 186 mm length | 1 |

Accessory sheet total: `82.4`.

## Fastener Sheet Highlights

| Item | Specification | Quantity |
|---|---|---:|
| Button-head socket screw | M3 x 8 | 31 |
| Button-head socket screw | M3 x 12 | 24 |
| Button-head socket screw | M3 x 16 | 8 |
| Button-head socket screw | M3 x 22 | 12 |
| Button-head socket screw | M3 x 40 | 2 |
| Countersunk socket screw | M3 x 8 | 12 |
| Countersunk socket screw | M3 x 16 | 2 |
| Countersunk socket screw | M3 x 40 | 7 |
| Flat-head socket screw | M3 x 6, 5.5 head diameter, 1 mm thick | 6 |
| Square nut | M3 x 6 x 2 | 74 |
| Square-head screw | M4 x 8 | 1 |
| Magnet | 6 mm diameter, 3 mm thick | 6 |
| Damping hinge | Small 1.5 kgf.cm right, non-adjustable | 1 |
| Damping hinge | Small 1.5 kgf.cm left, non-adjustable | 1 |

Fastener sheet total: `17.065`.
