# Fleet View — Feature Demo Transcript

## Introduction

Hi, this is a walkthrough of Fleet View — FlytBase's real-time fleet monitoring dashboard. Fleet View is where operators go to keep eyes on their entire drone fleet from a single screen.

Let me show you what it looks like and how to use it.

---

## Accessing Fleet View

To get to Fleet View, click the navigation drawer icon at the top left — that's the grid or menu icon. From the drawer, select the Fleet tab.

You'll land on the Fleet View dashboard. On the left you have the device table. In the center is the live 3D map. And below the device table is the live video panel.

All three update in real time and are linked to each other — selecting a drone in the table highlights it on the map, and the video panel shows the feed from whichever drone is currently pinned.

---

## Reading the Device Table

The device table shows every drone registered to your organization. Each drone is its own row.

On the far left of each row is a colored status dot. Orange means the drone is armed and active — it's flying. Green means it's online and connected but not currently flying. Grey means it's offline and not connected.

By default the table sorts automatically so armed and online drones are always at the top. You don't need to hunt for the active ones — they rise to the top on their own.

---

## Telemetry Columns

Let me scroll through the columns. Starting from the left you have: drone name, then altitude — you can switch between RLT, relative to take-off point, or AGL, above ground level — and battery percentage.

If you click the battery cell you get more detail: remaining flight time and charging state.

Keep scrolling right and you'll see: Distance to Home, Operation State — Mission, Hovering, Landing, RTH, and so on — System State, which pilot is controlling it, which site it's at, Command Status showing recent command results, Device Health which you can click to see diagnostic warnings, RTK Status showing positioning quality and satellite count, and Environmental Alerts for weather or airspace.

There's more telemetry than fits on screen. Scroll the table horizontally to reach the later columns.

---

## Sorting

Most columns are sortable. Click a column header to sort. The most useful ones are Battery — sort ascending to surface the drones running low — and Altitude. Armed and online drones stay pinned toward the top regardless of sort order.

---

## Auto-Selection

Above the table there's the Auto control. Click it and you'll see two options: Armed and Online.

Turn on Armed and the table automatically keeps all armed drones in the active view. Turn on Online and it does the same for online drones. Turn both on and you get all currently active drones surfaced automatically. The moment a drone changes state — say it arms — it appears in the view. When it lands and disarms it steps back.

Auto-Selection works alongside manual selections. If you manually pin a drone, it stays in view even if it doesn't match the Auto filter.

---

## Pin Rotation

Next to Auto is the Pin control. Click it to see the rotation intervals: off, 10 seconds, 15 seconds, 30 seconds, 1 minute, 5 minutes.

When rotation is on, Fleet View cycles the spotlight — the pinned drone, the map center, and the video feed — through all the drones currently in the active view, one by one on that timer. When it reaches the end of the list it loops back.

This is powerful for wall displays or operations rooms. Set it to 30 seconds on a big screen and you get a hands-free tour of every active drone's position and camera feed.

If you need to investigate something, just manually click a drone. Rotation stops and stays on that drone. You can re-enable rotation whenever you're ready.

---

## Filtering by Site and Pilot

Above the table you also have Sites and Pilots filters.

The Sites filter lets you pick one or more sites. The table instantly narrows to only drones at those locations. Useful if you're focused on a specific location during a busy operation.

The Pilots filter shows each pilot with a live count of how many drones they're flying right now. Select one or more pilots to see only their drones. Your own account is clearly marked so you can filter to yourself quickly.

These filters combine with each other and with Auto-Selection. For example: armed drones, at Site A, flown by Pilot X. Whenever any filter is active, a summary appears below the controls and you can clear individual filters or all of them at once.

Filter selections are saved — next time you open Fleet View the same filters are active.

---

## Launching a Mission

To launch a mission, select a single drone row. You'll see action buttons appear. Click Launch Mission.

A panel opens. You can search for missions by name, or filter by Tags or Type — Path or Grid. Click a mission to see a preview: total distance, estimated flight time, number of waypoints, what happens at the end — Return to Home, hover, land — and an elevation profile chart.

When you're happy with the selection, click Next. You'll step through the preflight checklist — things like GPS signal, battery level, obstacle clearance. If everything checks out, click Launch and the drone takes off.

Missions already in progress show an In Progress badge. Partially completed missions show an Incomplete badge and can be resumed.

---

## Go to Location

Go to Location is for ad-hoc repositioning. Select a drone, click Go to Location.

You can either click directly on the map — a marker drops and the lat/long fill in automatically — or type the coordinates by hand.

Then set the altitude, choose RLT or AGL, and set the speed. You'll see the total distance and an elevation profile to the destination. Click Next, complete the preflight checklist, and Launch. The drone flies straight to that point and hovers there waiting for your next instruction.

---

## Alarms Panel

Click the bell icon at the top to open the Alarms panel. Alarms come from external systems — security cameras, video management systems, intrusion sensors — connected to your FlytBase account.

Alarms are listed newest first, grouped by date. Each one has a severity color and shows whether the response is Auto — launches automatically — or Manual — waits for you.

To respond to a manual alarm, click it to expand it. You'll see which drone is assigned, what action it will take — Mission or Go to Location — and the automation flow behind it. Click Send drone. You'll step through the same Mission or Go to Location flow we just covered. After you launch, the alarm status updates live through in-progress to Responded.

If a response fails there's a Rerun button. You can also view the full alarm log and flow log for audit purposes.

---

## Performance Mode

Finally, the Performance control. By default Fleet View runs in Standard mode at 30 frames per second. If you're managing a lot of simultaneous video feeds and you want to reduce the load on your browser, switch to Optimized. That drops to 15 FPS but keeps every feed live.

---

## Wrapping Up

Fleet View is at its most powerful when you combine all of these features together. Filters narrow the list to the drones you own. Auto-Selection keeps only the active ones in view automatically. Pin Rotation tours those drones on a timer without any input from you.

The result is a self-managing operations dashboard — the right drones, their live telemetry, their map positions, and their camera feeds, all updating continuously.
