---
type: Release
title: Cockpit 3.0 Video Overlays Update
description: See zones on the live video, get warned before a No-Fly Zone, draw on the map and video for your team, and send the drone to a point you see on the video.
tags: [cockpit, video, nfz, zones, safety, live-annotation, go-to-location]
status: draft
---

# Cockpit 3.0: Video Overlays Update

This update adds four features to the live video in Cockpit. Each one is switched on or off in the **Video overlays** panel, opened with the button at the bottom-right of the video.

| Feature | What it does |
|---|---|
| Zone Overlays | Draws No-Fly Zones and the Geofence on the live video |
| Zone Proximity Warnings | Warns on the video, and blinks the zone on the map, when the drone gets close to or enters a No-Fly Zone |
| Live Annotation | Lets you draw on the map and the live video so everyone watching the same drone sees it |
| Go to Location from Video | Sends the drone to a point you right-click on the live video |

## Release details

- **Documentation set:** Cloud.
- **Feature flag:** to confirm. Is any of these features behind a feature flag?
- **Video link:** none yet.
- **Media** (all in this folder). One GIF per feature for the release note, and step screenshots for the doc pages:
  - Zone Overlays: `zone-overlays.gif`
    - `zone-overlays-01-video-overlays-panel.png`
    - `zone-overlays-02-nfz-and-geofence-on-video.png`
    - `zone-overlays-03-nfzs-hidden.png`
    - `zone-overlays-04-geofence-hidden.png`
  - Zone Proximity Warnings: `zone-proximity-warnings.gif`
    - `zone-proximity-01-red-glow-distance-and-blinking-nfz.png`
    - `zone-proximity-02-toggle-in-video-overlays.png`
  - Live Annotation: `live-annotation.gif`
    - `live-annotation-01-toggle-in-video-overlays.png`
    - `live-annotation-02-toolbar-freehand-selected.png`
    - `live-annotation-03-freehand-circle-on-video.png`
    - `live-annotation-04-direction-arrow.png`
    - `live-annotation-05-symbol-menu.png`
    - `live-annotation-06-symbols-placed.png`
    - `live-annotation-07-ink-colour-menu.png`
    - `live-annotation-08-marks-removed-with-tick.png`
  - Go to Location from Video: `go-to-location-from-video.gif`
    - `go-to-location-01-right-click-menu.png`
    - `go-to-location-02-panel-with-coordinates.png`
    - `go-to-location-03-launch.png`
    - `go-to-location-04-target-pin-on-video.png`

## Zone Overlays

Cockpit can now draw your zones on the live video. No-Fly Zones (NFZs) appear as red shaded areas on the ground in the camera feed, labelled with the zone's name and a distance. The Geofence appears as a green outline. You can show or hide each type of zone, and hide the NFZ labels, without turning the whole overlay off.

### What it does

- Draws NFZs on the live video as red shaded areas on the ground.
- Labels each NFZ with its name and a distance, for example **274.6 m**.
- Draws the Geofence on the live video as a green outline.
- Lets you show or hide NFZs and the Geofence separately, with the eye icon next to each.
- Lets you hide the NFZ labels while the zones stay visible, with the tag icon next to **NFZs**.

### Before you start

- You are viewing the drone's live video in Cockpit.

### Show zones on the video

1. Click the **Video overlays** button at the bottom-right of the video.
2. Turn on **Zone overlays**.
   - NFZs appear as red shaded areas, each with its name and a distance.
   - The Geofence appears as a green outline.
3. Expand **Zone overlays** to see its options. The **NFZs** and **Geofence** rows appear underneath it.

### Show or hide NFZs or the Geofence

1. Open **Video overlays** and expand **Zone overlays**.
2. Click the eye icon next to **NFZs** to hide the NFZs. Click it again to show them.
3. Click the eye icon next to **Geofence** to hide the Geofence. Click it again to show it.

### Hide NFZ labels

1. Open **Video overlays** and expand **Zone overlays**.
2. Click the tag icon next to **NFZs**. The NFZ labels disappear and the zones stay on the video. Click it again to show the labels.

### Switch the feature on or off

1. Click the **Video overlays** button at the bottom-right of the video.
2. Turn **Zone overlays** on or off.

Turning it off removes the zones from the video.

### Not yet known (please answer before publishing)

These are not shown in the recording, so they are left out above rather than guessed:

- What is the distance on an NFZ label measured from: the drone, or the zone's nearest edge?
- Is **Zone overlays** on or off by default, and does Cockpit remember the setting?
- Does it work on the FPV and dock cameras, or only on the payload camera?
- Do the zones stay fixed to the ground as the camera moves or zooms?
- Which zones are drawn: all zones of the site, or only zones synced to the drone?
- Limitations, error messages and troubleshooting.

## Zone Proximity Warnings

Cockpit now warns you when the drone is getting close to a No-Fly Zone (NFZ). A red glow appears on the edge of the live video, on the side where the zone is. A small label shows how far away the zone is and which way it lies. At the same moment, the zone blinks on the map. If the drone enters the zone, the label changes to **IN NFZ**.

### What it does

- Watches every NFZ that is synced to the drone you are flying.
- **When the warning starts:**
  - When the drone is hovering, moving slowly, or flying alongside the zone, the warning starts **40 m** from the zone.
  - When the drone flies toward the zone faster than 5 m/s, the warning starts further out, so you still get about **8 seconds** to react. At **10 m/s** it starts **80 m** away. At **15 m/s** it starts **120 m** away.
- Shows a red glow on the edge of the video, on the side where the zone is. The glow gets stronger and wider as the drone gets closer.
- Shows a label at the top of the video with **NFZ**, an arrow pointing toward the zone, and the distance to its edge. The distance uses your chosen units.
- Takes the camera direction into account. If you turn the camera, the glow and the arrow move so they still point at the zone.
- **When the glow pulses:** the glow and the warning icon start to pulse at **16 m** from the zone, or **32 m** when flying toward it at 10 m/s.
- Shows **IN NFZ** in a larger, pulsing label when the drone is inside a zone.
- Warns about several zones at once. Each zone gets its own glow. The label shows the most urgent zone.
- Blinks the matching zone on the map, and in the **Maneuver view**, while the warning is showing. The zone outline stays in place; only the fill blinks.
- Ignores brief position glitches. A warning has to hold for half a second before it appears, so a single bad GPS reading does not flash the screen.
- Avoids flickering at the boundary. Once a warning is showing, it stays on until the drone is about 20 m further away than where it started.
- Clears straight away when the drone flies clearly away from the zone.
- Tells you when zone data is missing, so missing data never looks like clear airspace.

### What it does not do

- It does not stop, slow or turn the drone. It only warns you. You stay in control.
- It does not play a sound.
- It does not warn about Geofences. Only No-Fly Zones trigger warnings.
- It does not consider altitude. The distance is measured along the ground to the nearest edge of the zone.
- It only knows about zones that are synced to the drone. A zone that has not been synced is not checked.
- It only works on the payload camera feed. It does not appear on the FPV or dock camera.

### Before you start

- The NFZs you care about are synced to the drone.
- The payload camera feed is showing in Cockpit.
- **Zone proximity warnings** is switched on in the **Video overlays** panel. It is on by default.

### Read a warning on the video

1. Fly the drone as normal with the payload camera feed showing.
2. When the drone gets close to an NFZ, a red glow appears on the edge of the video, on the side where the zone is.
3. Look at the label at the top of the video:
   - **NFZ** with an arrow and a distance means the drone is approaching a zone. The arrow points toward the zone. The distance is how far the drone is from its edge.
   - **IN NFZ** means the drone is inside the zone.
4. Watch the glow. A stronger, pulsing glow means the drone is very close.
5. Fly away from the zone. When the drone is clearly moving away, the glow and label disappear.

### Find the zone on the map

1. While a warning is showing on the video, look at the map.
2. The zone that triggered the warning blinks.
3. If more than one zone is close, each of them blinks.
4. The blinking stops when the warning clears.

The same blinking also shows in the **Maneuver view**.

### Switch the feature on or off

1. Click the **Video overlays** button at the bottom-right of the video.
2. Turn **Zone proximity warnings** on or off.

Turning it off removes the glow and label from the video and stops the zones blinking on the map. Cockpit remembers your choice the next time you open it.

While a safety overlay is off or cannot show, a small amber dot appears on the **Video overlays** button. A red dot means a safety overlay hit an error.

### Tips

- Keep the warnings on during flights near NFZs. They are a second pair of eyes, not a replacement for checking the map.
- Use the arrow, not just the glow, to judge direction. The arrow always points to the nearest edge of the zone.
- When you fly fast, the warning appears from further away. Slow down if you want a closer, more precise approach.
- When you hover near a zone, the warning stays on as long as you are within 40 m, because a hovering drone can drift in any direction.
- If you see **Zone data unavailable — warnings off**, do not assume the area is clear. Check the zones on the map before you continue.

### Troubleshooting

| What you see | What to check |
|---|---|
| "Zone data unavailable — warnings off" | Cockpit could not load the zones for this drone, so no warnings can show. Check the zones on the map yourself and wait for the zone data to load. |
| "No zones synced to this drone." | No zones are synced to the drone. Sync the zones to the drone if you need warnings. |
| "Waiting for a video feed." | The camera feed is not showing yet. Wait for the video to appear. |
| "Only available on the payload camera." | Switch the video to the payload camera. |
| No glow appears, but you are near a Geofence | This is expected. Warnings only cover No-Fly Zones. |
| No glow appears near an NFZ | Make sure **Zone proximity warnings** is on in **Video overlays**, and that the zone is synced to the drone. |
| "Zone proximity warnings turned off due to an error. Retry from the layers panel." | Open **Video overlays** and turn **Zone proximity warnings** on again. You can close the message with its **×** button. |
| "This overlay hit an error and was switched off. Toggle to retry." | Turn **Zone proximity warnings** on again in **Video overlays**. |
| An amber dot on the **Video overlays** button | A safety overlay is switched off or cannot show right now. Open **Video overlays** to see which one and why. |

## Live Annotation

You can now point things out to your team while a drone is flying. Draw a line, an arrow or a circle, or drop a symbol such as **Person** or **Hazard**, on the map or on a live camera feed. Everyone who is watching the same drone in Cockpit sees your mark straight away, in the same place, with your name next to it. Marks are short-lived. They fade on their own after a few seconds, so the screen does not fill up.

### What it does

- Lets you draw on the map and on the live video of any camera that is streaming.
- Offers these tools:
  - **Freehand**: draw a free line.
  - **Direction**: drag to draw an arrow.
  - **Area**: drag to draw a circle around something.
  - **Place a symbol**: drop a **Point of interest**, **Person**, **Vehicle** or **Hazard** marker with one click.
- Lets you pick an ink color: **Red**, **Orange**, **Amber**, **Green** or **Blue**. Red is the default.
- Shows your marks to everyone watching the same drone. On their screen, each mark carries your name and profile picture.
- Places map marks at the real location, so they line up for every viewer, whatever their zoom or view.
- Keeps video marks on the camera they were drawn on. A mark on one camera does not appear on another.
- Lists who is drawing in a **Live annotating** panel, with buttons to jump to their marks or hide them.
- Remembers the last tool you used.

### What it does not do

- It does not save marks. Marks are not kept after they fade and cannot be found later.
- It does not replace map annotations. For points, lines and shapes that should stay on the map, use the regular annotation tools.
- Marks on the video stay at the same spot in the picture. They do not follow the ground when the camera moves.
- It does not send anything to the drone. Marks are only shown to people in Cockpit.

### Before you start

- You are viewing a drone in Cockpit.
- To draw on video, the camera feed must be streaming.
- Your teammates must be viewing the same drone to see your marks.

### Open the toolbar

1. Click the **Live annotation** button (the pen icon). You will find it in the map controls, and in the controls at the bottom-right of the video, next to **Video overlays**.
2. The toolbar opens just above the button. The last tool you used is ready to draw.
3. To move the toolbar, drag it by the grip on its left edge or by its bottom strip.

The bottom strip shows the selected tool's name. While a tool is ready, it also shows how to move the map. While no tool is selected, it shows **Idle**.

To close the toolbar, click **Hide toolbar** (the X), or click the **Live annotation** button again. When the toolbar is closed, you can no longer draw.

### Draw on the map

1. Open the toolbar and choose a tool.
2. Draw on the map:
   - **Freehand**: hold the left mouse button and drag.
   - **Direction** and **Area**: hold the left mouse button and drag from start to end.
   - **Place a symbol**: click the symbol button, choose a symbol from the list, then click the map where you want it.
3. Let go. Your mark stays on screen for a few seconds, then fades for everyone.

While a tool is selected, dragging with the left button draws instead of moving the map. To move the map, **Right-drag to pan the map**. On a Mac, use a **Two-finger click-drag to pan the map**.

To stop drawing without closing the toolbar, click the selected tool again.

### Draw on the video

1. Open the toolbar and choose a tool.
2. Draw on the live picture the same way as on the map. Click and drag inside the video, or click once to place a symbol.
3. Your mark appears on that camera for everyone watching it.

### How long marks last

- A **Freehand** line fades about 4 seconds after you finish it.
- A **Direction** arrow, an **Area** circle and every symbol fade about 10 seconds after you place them.
- A new **Freehand** line, arrow or circle replaces your previous one of the same type on the same map or camera.
- Symbols add up instead of replacing each other. You can have up to 8 symbols on the map or on one camera at a time. When you add more, your oldest one is removed.

### See and follow what others draw

When someone else draws, the **Live annotating** panel appears. It lists each person drawing, with their name and picture, and where they drew: **Map** or **Video**.

- **Take me there**: next to a **Map** entry. Moves your map to that person's latest mark. Your map stays in 2D or 3D, whichever you were using.
- **Show me**: appears when the mark is somewhere you cannot see right now, for example a camera feed you are not showing. Cockpit opens that map or feed for you.
- **On screen**: the video mark is already visible to you.

To make the panel smaller, click **Hide the list**. It shrinks to a small strip that shows the colors of the people drawing and a count, for example "2 people drawing". Click **Show details** to open it again.

The panel closes on its own when no one else has marks on screen.

### Hide other people's marks

You have three ways to hide marks. These only change your own screen. Other viewers still see the marks.

- **One mark**: click the small X next to the person's name on the mark (**Erase for me**).
- **Everything on screen now**: in the toolbar, click **Clear their marks from my view** (the eraser). New marks drawn after this still appear.
- **One person**: in the **Live annotating** panel, click **Hide this person's marks** next to their name. Their name is crossed out and their new marks stay hidden. To undo, click **Show this person's marks again**.

### Remove your own marks

In the toolbar, click **Remove my marks for everyone** (the bin). All your marks, on the map and on every camera, disappear for everyone.

Both clear buttons show a tick for a moment when they work. They are greyed out when there is nothing to clear.

### Switch live annotations on or off

You can stop seeing other people's marks altogether. This is one setting, shared by the map and the video.

- On the map: open the map layers panel and turn **Live annotations** on or off.
- On the video: click **Video overlays** at the bottom-right of the video and turn **Live annotations** on or off.
- In the **Live annotating** panel: click **Turn off live annotations**, or **Show annotations** to turn them back on.

When it is off, you still see your own marks and you can still draw. Others still see what you draw. Turning it off only hides marks from other people. If someone draws while it is off, the panel still appears as a small strip, so you know you are missing something. Cockpit remembers your choice the next time you open it.

### Tips

- Talk while you draw. Marks are short-lived on purpose. They work best as "look here" while you speak.
- Use a symbol for something that matters a bit longer, such as a person or a hazard. Symbols last longer than freehand lines and do not replace each other.
- Agree on colors with your team, for example red for hazards.
- If you cannot move the map, check whether a tool is selected. Click the selected tool again, or right-drag to pan.

### Troubleshooting

| What you see | What to check |
|---|---|
| Dragging on the map draws a line instead of moving the map | A tool is selected. Right-drag to pan (on a Mac, two-finger click-drag), or click the selected tool again to stop drawing. |
| "Not shared — connection lost" in the toolbar | Your mark did not reach the others. Your own screen still shows it. Check your internet connection and draw it again. |
| "Waiting for a video feed." / "Waiting for the feed to start." next to **Live annotations** in **Video overlays** | The camera feed is not streaming yet. Wait for the video to appear. |
| Teammates say they cannot see your marks | Make sure they are viewing the same drone. For video marks, they must be showing the same camera. Ask them to check that **Live annotations** is on and that they have not hidden your marks. |
| You cannot see someone's marks | Check that **Live annotations** is on. In the **Live annotating** panel, check that the person is not crossed out. If they are, click **Show this person's marks again**. |
| The **Live annotating** panel shows **Show me** | The mark is on a map or camera you are not showing. Click **Show me** to open it. |
| **Clear their marks from my view** or **Remove my marks for everyone** is greyed out | There is nothing to clear right now. |
| "This overlay hit an error and was switched off. Toggle to retry." | Turn **Live annotations** off and on again in **Video overlays**, or click **Show annotations** in the **Live annotating** panel. |

## Go to Location from Video

You can now send the drone to a place you see in the live video. Right-click a point on the ground in the camera feed, and Cockpit works out where that point is on the map. It then opens the usual **Go to Location** panel with the coordinates already filled in. While the drone flies, a pin on the video marks the target so you can watch it get closer.

### What it does

- Right-clicking the live payload camera feed shows a **Go to location** option. The option also shows how far the point is from the drone, for example "120 m away".
- Opens the standard **Go to Location** panel with **Latitude** and **Longitude** pre-filled. You still choose altitude and speed in that panel, the same way you do for a go-to started from the map.
- Shows the chosen point on the map next to the drone.
- Pins the target on the video. The pin stays fixed to the ground as the camera moves. Its label shows the distance and the go-to altitude.
- Keeps the video and the map in sync. If you move the point on the map, the pin on the video moves too.
- Shows a **Reached location** message on the video when the drone arrives.
- Lets you replace a running operation: if a mission or another go-to is running, the option reads **Stop & Go to location**. Choosing it stops the current operation and then opens the Go to Location panel.

### What it does not do

- It does not point the camera at the target. The gimbal stays where you left it.
- It only works on the payload camera feed. It does not work on the FPV or dock camera.
- It does not work on sky or above the horizon. The point you click must be on the ground, within 5 km of the drone.
- It does not work while split-screen is on.
- It has no stop or pause of its own. Use the normal Cockpit flight controls for that.

### Before you start

- The dock is online and you have control of the drone.
- In **Drone settings → Telemetry**, the **Fast** telemetry link is selected.
- The payload camera feed is streaming and split-screen is off.
- **Go to location from video** is switched on in the **Video overlays** panel. It is on by default.

### Send the drone to a point on the video

1. Find the place you want to fly to in the live payload camera feed.
2. Right-click the ground at that place.
   - The menu briefly shows **Working out where that is…** while Cockpit locates the point.
   - The option then shows **Go to location**, with the distance from the drone underneath.
3. Click **Go to location**. The **Go to Location** panel opens with the coordinates filled in, and the map shows the point.
4. Set **Waypoint altitude** and choose **RLT** (relative to the take-off point) or **AGL** (above ground level). Then check **Drone speed** and **Total Distance**.
5. Click **Launch**. If the drone is not armed, the button reads **Next** instead.
6. Watch the pin on the video. Its label shows the remaining distance and the altitude, for example "ALT 60 m AGL".
7. When the drone arrives, **Reached location** appears at the top of the video for a few seconds.

To cancel before launch, click **Cancel** in the Go to Location panel, or click outside the menu to close it.

### Switch the feature on or off

1. Click the **Video overlays** button at the bottom-right of the video.
2. Turn **Go to location from video** on or off.

Turning it off removes the right-click option, the pin and the arrival message. Cockpit remembers your choice the next time you open it.

### Tips

- Tilt the camera down toward the ground for the most accurate point. At shallow angles near the horizon, a small change in where you click can mean a big change on the ground.
- If you are far from the target, fly closer before you click.
- You can zoom the camera in before you right-click. Cockpit takes the current zoom into account.
- Check the point on the map before you launch, especially in hilly areas or near tall buildings.

### Troubleshooting

| What you see | What to check |
|---|---|
| The right-click menu does not appear on the video | Make sure **Go to location from video** is on in **Video overlays**, and that you are on the payload camera with split-screen off. |
| "Requires the Fast telemetry link." | Click **Open telemetry settings** and choose the **Fast** link. |
| "Paused — DRC connection lost." | The fast link to the drone has dropped. Wait for it to reconnect. |
| "Waiting for a video feed." / "Waiting for the feed to start." | The camera feed is not streaming yet. Wait for the video to appear. |
| "Only available on the payload camera." | Switch the video to the payload camera. |
| "Hidden while split-screen is on." | Turn off split-screen. |
| "That's outside the picture." | You clicked on the black bars around the video. Click inside the picture. |
| "Camera position unavailable." | Cockpit does not have up-to-date drone position or camera angle. Wait a few seconds and try again. |
| "Terrain unavailable here." | Ground height data is not available for this area, so the point cannot be located. Pick the point on the map instead. |
| "No ground found within range." | You clicked on sky, at the horizon, or on ground more than 5 km away. Tilt the camera down and click on the ground. |
| "The aircraft's altitude reads at or below the ground here." | The drone's reported altitude is at or below the ground. Check the drone's altitude before using this feature. |
| "Too uncertain at this angle — the ground is nearly edge-on. Tilt down or fly closer." | The camera is too close to level. Tilt the camera down or fly closer, then try again. |
| "Dock offline" | Wait for the dock to come back online. |
| "Drone control needed" | Click **Take control**, then right-click again. |
| "Not available right now" | The drone cannot start a go-to in its current state. |
| "Stop timed out. Tap to retry." / "Failed to stop. Tap to retry." | The running operation did not stop. Click the option again to retry. |
| "This overlay hit an error and was switched off. Toggle to retry." | Turn **Go to location from video** off and on again in **Video overlays**. |
