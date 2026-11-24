English | **[中文](README.zh-CN.md)**

# PitchTrace 3D

PitchTrace 3D is a 3D tracking and replay project for football (soccer) matches. Four stereo camera rigs around the pitch capture the action; Mac mini edge nodes reconstruct players, the ball, and skeletal motion, letting viewers freely switch angles to review passes, shots, and challenges.

The [live viewing frontend](live/web/) is runnable. It ships with a built-in 60-second match and can also switch to a WebSocket live data feed and external HLS footage. After cloning, you can open the [offline viewing page](live/web/dist/index.html) directly in a browser.

## Subprojects

| Subproject | Deliverables & responsibilities | First-version tech stack |
| --- | --- | --- |
| [PitchTrace Edge · edge compute](edge/) | A buildable SwiftPM skeleton exists; the plan is to capture, reconstruct, and emit scene snapshots, video clips, and exposure-time indexes on Mac mini. | Swift, Core ML, Vision, C++/OpenCV, FFmpeg, VideoToolbox. [Edge implementation](docs/02-technical-design.md#pitchtrace-edge) |
| PitchTrace Live · live (`live/`) | Receives, archives, and distributes the above data; provides 3D viewing, live replay, and manual review. Includes server and web. | Node.js/TypeScript, Fastify, WebSocket, HLS, Vite, Three.js. [Live implementation](docs/02-technical-design.md#pitchtrace-live) |

The browser frontend supports both v1 and v2 scene snapshots; a [sample gateway](live/server/) provides v1 playback. Edge recognition, the full v2 gateway, and original-footage synchronized review based on exposure-time indexes are still in planning. [Scene & media interface](docs/02-technical-design.md#edge-live-contract)

## Project documentation

| Document | Purpose |
| --- | --- |
| [Detailed requirements](docs/01-requirements.md) | Defines the match flow, feature scope, and on-site acceptance criteria. |
| [Technical design](docs/02-technical-design.md) | Designs the eight-channel capture, Mac mini 3D reconstruction, data distribution, and review pipeline. |
| [Development plan](docs/03-development-plan.md) | Schedules site surveys, development, live-shoot acceptance, and release; names owners and release conditions per stage. |

## Repository structure

```
pitchtrace-3d/
├── docs/                      Requirements, technical design, and development plan
├── edge/                      PitchTrace Edge · pitch-side edge compute (Swift / SwiftPM)
│   ├── Package.swift
│   └── Sources/PitchTraceEdge/
└── live/                      PitchTrace Live · live and viewing
    ├── web/                   Browser viewing frontend (Vite + Three.js)
    │   ├── src/               app / match / receiver / live-frame / pitch / rig / media / protocol
    │   ├── test/              Built-in match and protocol cases (node:test)
    │   └── dist/index.html    Build output, openable offline
    └── server/                Local playback gateway (v1, Node.js)
        ├── server.mjs         Source
        └── server.cjs         Bundled output
```

For each subproject, see [edge/README.md](edge/README.md), [live/web/README.md](live/web/README.md), and [live/server/README.md](live/server/README.md); for AI-agent guidance, see [AGENTS.md](AGENTS.md).

## Build & test

Requires Swift 6.0+ and Node.js 20.19+ (or 22.12+). The commands below run from the repository root.

```sh
# Edge · build (currently an empty entry skeleton, exits immediately when run)
swift build --package-path edge
swift run  --package-path edge pitchtrace-edge

# Live Web · develop, test, and build the single-file frontend
cd live/web && npm ci && npm run dev      # dev server
cd live/web && npm test                   # checks the built-in match and data protocol
cd live/web && npm run build              # generates dist/index.html

# Live Server · build and start the playback gateway
cd live/server && npm ci && npm run build # bundles server.mjs into server.cjs
node live/server/server.cjs               # listens on 127.0.0.1:8766
```

## Local playback

To play back a WebSocket data stream, first run `npm run build` in `live/web`, then from the repository root run the following command and open <http://127.0.0.1:8766/>. Running the service requires Node.js 20 or higher.

```sh
node live/server/server.cjs
```

It listens on `127.0.0.1:8766` by default. On a trusted LAN you can set `PITCHTRACE_HOST` and `PITCHTRACE_PORT` to the Mac's LAN IP and port; viewers open the page at the corresponding address. For camera controls, data-frame format, and how to rebuild the gateway, see the [viewing frontend guide](live/web/README.md) and the [gateway interface guide](live/server/README.md).

## License

Original code is released under the [MIT License](LICENSE). Third-party models, animations, and dependencies used by the demo retain their own licenses; see [asset attribution & licenses](live/web/licenses/).
