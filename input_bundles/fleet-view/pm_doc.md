# PM Document — Fleet View (Real-Time Fleet Monitoring)

## What the Feature Is

Fleet View is a live command center for an operator's entire drone fleet. It brings every drone, dock, and pilot together on a single screen, enabling real-time monitoring, telemetry reading, and action-taking without jumping between individual device pages.

It is designed for organizations running anything from a single site to dozens of autonomous drones across multiple locations.

[SCREENSHOT: fleet-view-dashboard-overview.png]

---

## What Problem It Solves

Before Fleet View, operators had to navigate to each drone individually to check its status, altitude, battery, or camera feed. During active multi-drone operations this created a constant context-switching burden, increased the risk of missing a drone in distress, and made cross-fleet situational awareness nearly impossible.

Fleet View eliminates this by consolidating the entire fleet into a single, self-updating view. An operator can read telemetry, watch video, issue commands, and respond to alarms — all from one dashboard.

---

## How It Works

Fleet View is organized into three synchronized areas:

- **Device Table (left):** A live sortable table listing every drone with real-time telemetry. Armed and online drones always surface to the top automatically.
- **Interactive 3D Map (center):** Plots each drone at its exact GPS location with mission paths and home points. Selecting a drone in the table highlights it on the map and vice versa.
- **Live Video Panel (below the table):** Streams the camera feed from the pinned drone. Supports a grid layout to watch multiple feeds side by side.

All three areas share the same live data pipeline, so the telemetry, map position, and video always refer to the same state of the drone.

[SCREENSHOT: fleet-view-device-table.png]

---

## Step-by-Step Usage

### Accessing Fleet View

1. Click the navigation drawer icon in the top-left corner of the FlytBase interface.
2. Select the **Fleet** tab.
3. The full fleet loads in the device table on the left, with the map and video panel populating automatically.

### Reading Drone Status

Each drone row begins with a colored status dot:

| Color | Status | Meaning |
|---|---|---|
| Orange | Armed | Drone is armed and active — flying a mission or under manual control |
| Green | Online | Powered on and connected, not currently armed |
| Grey | Offline | Not currently connected |

### Reading Live Telemetry

Each row shows the following columns (scroll horizontally to reveal all):

- **Altitude** — switchable between RLT (relative to launch) and AGL (above ground level)
- **Battery** — percentage, color-coded; click for remaining flight time
- **Distance to Home** — live distance from current position to home point
- **Operation State** — Mission / Hovering / Taking off / Landing / Manual / RTH
- **System State** — In progress / Idle / Landing
- **Pilot** — name with live online indicator
- **Site** — assigned site
- **Command Status** — in-progress / completed / failed command summary
- **Device Health** — alerts; click to expand diagnostics
- **RTK Status** — positioning accuracy and satellite count
- **Environmental Alerts** — weather and airspace warnings

### Using Auto-Selection

Open the **Auto** control above the table. Choose **Armed**, **Online**, or both. Fleet View will automatically keep only matching drones in the active view and update the moment drone status changes. Manual selections override Auto-Selection — a manually pinned drone stays in view regardless.

### Using Pin Rotation

Select the **Pin** control and choose a rotation interval (10s, 15s, 30s, 1m, or 5m). Fleet View cycles the spotlight — map focus, video feed, and table highlight — through each drone in the active list on the chosen timer. Manually pinning a drone stops the rotation and focuses on that drone.

[SCREENSHOT: fleet-view-active-mission.png]

### Filtering by Site and Pilot

- **Sites filter:** Select one or more sites to narrow the table to drones at those locations.
- **Pilots filter:** Select one or more pilots to show only their drones. The live drone count next to each pilot name shows workload at a glance.

Filters combine — for example, armed drones at a specific site controlled by a specific pilot. A summary of active filters appears below the controls. Each filter can be cleared individually or all at once. Selections persist across sessions.

### Launching a Mission from Fleet View

1. Select a single drone row.
2. Click **Launch Mission**.
3. Browse or search the mission library. Filter by Tags or Type (Path / Grid).
4. Select a mission to see its distance, estimated flight time, waypoint count, finish action, and elevation profile.
5. Click **Next** → step through the preflight checklist → **Launch**.

Missions already running show an **In progress** badge. Partially completed missions show an **Incomplete** badge and can be resumed.

### Go to Location

1. Select a single drone row.
2. Click **Go to Location**.
3. Click a point on the map (coordinates fill in automatically) or type Latitude and Longitude.
4. Set altitude (RLT or AGL) and speed.
5. Review distance and elevation profile.
6. Click **Next** → preflight checklist → **Launch**.

The drone flies to the point and hovers on arrival.

### Responding to Alarms

Click the bell icon to open the Alarms panel. Alarms are grouped by date, newest first. Each alarm shows severity color, source icon, and whether the response is Auto (fires automatically) or Manual (requires confirmation).

To act on a manual alarm: select it → click **Send drone** → complete the mission or Go to Location flow → the drone dispatches. Watch the status update from in-progress to Responded. Failed responses show a **Rerun** option.

### Performance Mode

Switch between **Standard** (30 FPS) and **Optimized** (15 FPS) using the Performance control above the table. Use Optimized when managing a large number of simultaneous video feeds to reduce front-end load.

[SCREENSHOT: fleet-view-manual-control.png]

---

## Constraints and Limitations

- Action controls (Launch Mission, Go to Location) are only available when a single drone is selected and the operator has control of it.
- Non-docked drones do not support action controls from Fleet View — telemetry, video, and map position are available but no commands can be issued.
- Some actions are unavailable during maintenance mode or firmware updates.
- Alarms require alarm sources (security or VMS systems) to be configured in the account. Fleet View does not set up alarm sources — that is done in the integrations settings.
- Filter selections persist across sessions but are per-account, not per-device.

---

## Hardware Compatibility

Fleet View works with all drones and docks registered to a FlytBase account regardless of model. RTK Status is shown only for drones with RTK hardware. Cockpit video feeds require the drone to have a compatible payload and an active video stream.

---

## Key Use Cases

- **Operations centers and wall displays:** Enable Pin Rotation at 30s on a large display for a hands-free tour of all active drones.
- **Rapid incident response:** Send the nearest drone to a point of interest in seconds using Go to Location, or configure alarm integrations to dispatch automatically.
- **Multi-operator supervision:** Use the Pilots filter to monitor all drones controlled by a team member during training or a high-stakes flight.
- **Pre-shift readiness:** Enable the Online Auto filter to instantly see which drones and docks are connected and ready.
