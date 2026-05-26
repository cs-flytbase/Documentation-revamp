# **Site Pointers: Continuous Learning for Your Site**

---

**\[HERO GIF \- 12-15 seconds\]**

*Capture: A Verkos Detect Anything alert fires repeatedly on a fixed feature of the site (e.g., a maintenance shed read as a vehicle in thermal imagery, or a piece of fixed equipment misclassified as something else). Cut to the Site Pointers tab where the operator picks that image, draws a box around the object, types the correct label, and saves. Cut to a later flight where the drone returns to the same spot. The same object is in image, but no alert fires this time. The sequence communicates: "the agent learns from your correction, permanently."*

---

## **AI for Operations Has Been Static**

The system that flies a site on day one runs it the same way a year later. The operator running it has spent that year learning every nuance of the site. Today's AI systems aren't trained on any of it.

At night, a water-filled well gives off the same thermal signature as a shallow pond. From drone altitude, a maintenance shed can share the same outline as a parked vehicle. Structures specific to a site don't appear in any general training data, and get matched to whatever they most resemble.

There's no way to put what the operator knows about those objects into the system. The same false detection surfaces flight after flight. The operator dismisses it every time. Site knowledge stays trapped in their head.

---

## **Site Pointers: A Feedback Channel for Your Site Knowledge**

Site Pointers is that channel. When Verkos Detect Anything reads a site object wrong, you open the image, draw a box around the object, label what it actually is, and save. The correction is bound to the GPS location of the image and applies on every future flight that passes within a 100 m radius of that spot.

The teaching takes a moment. The result carries forward across every future flight, regardless of how the drone gets to that location.

You'll find Site Pointers as a new tab on the Verkos AI page, alongside Smart Missions and Agents.

**\[SCREENSHOT\]**

*Capture: Verkos AI page with the Site Pointers tab selected, showing the empty state. Header text "No pointers yet — When the AI Agent gets a detection wrong, capture an example here. The correction is published to the model so it improves at this site." should be clearly visible. The "+ Add pointer" button in the top right should be in image. This shows users where the feature lives.*

---

## **How the Correction Generalizes**

Each pointer is geographically scoped to a 100 m radius around the GPS position of the source image. Within that radius, Verkos applies the correction regardless of how the drone is flying:

* Waypoint missions  
* Go To Location  
* Manual flights  
* Any future mission type that brings a drone through the area

This means a single correction covers a meaningful patch of your site, not just a single waypoint. If the same fixed object is visible from several positions within that 100 m circle, you don't need to teach the agent at each one.

If the same kind of object appears elsewhere on your site outside that radius, you'll need to add a separate pointer for that location. Site Pointers is location-scoped by design. The same shed at site A and site B may be acceptable in one place and worth flagging in the other, and the operator decides per location.

---

## **Why This Is Foundational**

Site Pointers turns AI for operations from a static product into one that compounds. The site shapes the system. Every correction is a piece of operator site knowledge entering the agent and staying there. The longer Verkos runs at a site, the closer its alert stream gets to clean, without anyone retraining anything.

This is the foundation for AI that adapts to where it's deployed, not just to what it was trained on. You don't need a model retrain, a support ticket, or anyone outside your team. The operator runs the site, and the system gets sharper with use.

---

## **Adding a Pointer**

**Step 1: Open Site Pointers and click Add Pointer**

Navigate to **Verkos AI → Site Pointers**, then click **\+ Add pointer** in the top right.

**Step 2: Select the site and pick an image**

Choose the site from the dropdown. The image picker shows past detections from the Verkos Detect Anything agent at that site, grouped by date and location. Only images the agent has already analyzed appear here. You can't upload your own images or pull in images from outside Verkos.

Pick the image where the agent got something wrong.

**\[SCREENSHOT\]**

*Capture: Add Pointer modal with site dropdown set to a real site (e.g., "Alpha Site"), the left panel showing the date-grouped image grid with the count visible (e.g., "23 files" under a "Go to location" group). One image should be selected (highlighted with the checkmark) so users see the selection state. This shows the picker behavior and the constraint that only past Verkos detections are available.*

**Step 3: Confirm what the agent detected, draw what it actually is**

The **Detected Event** panel auto-fills with the original AI label from that image (e.g., "Industrial Towers"). Below it, drag a bounding box around the object the agent misread and type a short label for what it actually is (e.g., "Green silos").

**\[SCREENSHOT\]**

*Capture: Add Pointer modal in the labeling state. Detected Event panel showing the auto-filled original label with the AUTO badge. The drone image with a bounding box drawn around the misclassified object, and the corrected label visible above the box. The "Redraw" button should be in image so users know corrections are editable. This is the core teaching action. The screenshot must make it obvious.*

**Step 4: Confirm the agent and save**

In the **Tell us what the agent got wrong** section, select the agent the correction should apply to (Verkos Detect Anything). Add optional notes if helpful for your team's audit trail (e.g., "Please do not detect green silos").

Click **Save feedback**.

The pointer is now active. From the next flight onward, when any drone on this site passes within 100 m of the GPS position where the source image was captured, Verkos applies the correction.

---

## **Constraints & Requirements**

| Constraint | Detail |
| ----- | ----- |
| Image source | Only past detections from the Verkos Detect Anything agent at the selected site appear in the picker. Normal drone captured images cannot be selected. |
| Geographic scope | Each pointer applies within a 100 m radius of the GPS position where the source image was captured. |

---

## **Hardware Compatibility**

Site Pointers operates through the same cloud infrastructure as Verkos Detect Anything, so hardware requirements are unchanged.

| Hardware | Support |
| ----- | ----- |
| Dock 1 | ✓ Supported |
| Dock 2 | ✓ Supported |
| Dock 3 | ✓ Supported |
| Non-Dock | ✓ Supported |

---

## **Access**

Site Pointers is available now for any account with Verkos enabled. The new tab appears automatically on the Verkos AI page. There's no separate activation step.

If your account doesn't yet have Verkos, contact support@flytbase.com to request access.

---

## **Media Asset Summary**

| Asset | Type | Duration/Size | Priority | Purpose |
| ----- | ----- | ----- | ----- | ----- |
| Hero correction loop | GIF | 12-15 sec | **Required** | Shows the full loop: false detection → correction → no false detection on next flight |
| Site Pointers tab (empty state) | Screenshot | \- | **Required** | Where the feature lives, and the empty-state guidance |
| Add Pointer modal (image picker) | Screenshot | \- | **Required** | The image picker constraint and selection flow |
| Add Pointer modal (labeling state) | Screenshot | \- | **Required** | Bounding box, corrected label, and the Detected Event auto-fill |
| Site Pointers list (populated) | Screenshot | \- | **Required** | What the tab looks like once a few corrections are saved |
| Cockpit alert before/after | Side-by-side or split GIF | 8-10 sec | Recommended | Same drone position, same object, alert fires in "before" and stays silent in "after" |
| Field guide: 100 m radius | Diagram | \- | Recommended | Visual showing one pointer's coverage area on a site map, illustrating that flight mode doesn't matter inside the radius |

**Total required assets:** 4 screenshots, 1 GIF **Recommended:** 2 additional assets

---

For questions about Site Pointers or to request Verkos for your organization, contact support@flytbase.com.

