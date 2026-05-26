# Baseline Images for Accurate State-Change Detection

## Feature Overview
Verkos Detect-Anything Agents now support baseline images for state-change detection. Operators can upload a reference image of the "normal" state for any detection zone. During missions, Verkos compares the live feed against this baseline to detect changes — such as an open gate that should be closed, a missing vehicle, or new objects appearing in a restricted area.

## Problem This Solves
Previously, Verkos detection events relied solely on real-time object detection (person detected, vehicle detected, etc.). This works well for presence detection but fails at state-change scenarios where the question isn't "is something there?" but "has something changed?"

For example, a security patrol might need to verify that all gates are closed after hours. Without a baseline, the agent would need to understand what "closed" looks like for every gate — which varies by site. With a baseline image, the comparison is straightforward: does the current frame match the reference or not?

## How It Works
1. Navigate to the Verkos detection event configuration for your mission.
2. Select the detection zone where you want state-change detection.
3. Upload a baseline image showing the "expected" state.
4. Configure the sensitivity threshold (how different the current frame needs to be from the baseline to trigger an event).
5. Run the mission. Verkos will compare each frame in the detection zone against the baseline and flag any significant deviations.

## Key Details
- Baseline images are stored per detection zone, per mission.
- Operators can update baselines at any time — useful as environments change seasonally.
- Sensitivity threshold is adjustable from 0 (any change triggers) to 100 (only major changes trigger). Default is 60.
- Works alongside existing detection events — you can have both object detection and state-change detection on the same zone.
- Currently supported for DJI Dock 3 and Dock 2 drones only.

## Limitations
- Baseline comparison requires consistent camera angle. If the drone approaches from a different angle than the baseline was captured, accuracy drops.
- Night/low-light conditions reduce baseline comparison accuracy. Recommend using IR mode when available.
- Maximum 10 baseline images per mission.

## Assets
- Screenshot: detection event config screen with baseline upload option
- GIF: workflow showing baseline upload and threshold configuration
- Screenshot: live comparison view during mission showing baseline vs current frame
