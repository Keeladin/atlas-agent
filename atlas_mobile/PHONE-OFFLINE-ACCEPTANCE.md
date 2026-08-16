# Phone offline acceptance (dev-test only)

Not product architecture. Service workers need a secure/trusted origin for **install**. Offline proof requires the **host server path to be gone**.

`http://192.168.x.x:8765` is not a valid acceptance origin.

Do not use insecure-origin browser flags.

---

## Initial load (USB / adb reverse)

1. On the PC:

   ```bash
   cd /home/jaco-fouche/Projects/AtlasAgent/atlas_mobile
   python3 -m http.server 8765 --bind 127.0.0.1
   ```

2. USB debugging on. Then:

   ```bash
   adb reverse tcp:8765 tcp:8765
   ```

   (Chrome `chrome://inspect` port forward `8765` → `127.0.0.1:8765` is equivalent.)

3. On the phone Chrome, open **only**:

   ```text
   http://127.0.0.1:8765/
   ```

4. Confirm the service worker is installed and **active** for that origin.

5. Reload **once** while the forwarding path is still up so the app shell is cached.

---

## Actual offline test

6. Remove the forwarding path **before** claiming offline:

   ```bash
   adb reverse --remove tcp:8765
   ```

   **or** unplug USB / disable Chrome port forwarding.

7. Enable airplane mode.

8. Close Chrome / the PWA fully.

9. Reopen `http://127.0.0.1:8765/`.

10. The Atlas Mobile shell must load from the **service-worker cache** with **no reachable development server**.

11. Create/edit activities, Next, End Report, Copy.

12. Refresh/reopen again and confirm the report is still in IndexedDB.

---

## Pass / fail

**Pass** only if Atlas Mobile works after the host-server path has been made unreachable.

If the page only works while `adb reverse` or USB forwarding is still up, the test has **not** passed.

---

## Result

**Passed** — initial Atlas Mobile V1 user acceptance on the actual phone, offline, after the host-server path was unreachable.

Recorded from the device cycle:

- Form structure and general layout accepted for V1.
- Requested information is relevant; capture was not unnecessarily burdensome.
- Full report cycle usable offline (create/edit, Next persist, End Report, Copy).
- Completed report persistence/reopen included in that cycle.

Do not redesign or add fields without a new reviewed operational reason.
