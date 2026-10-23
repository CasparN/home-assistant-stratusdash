# StratusDash for Home Assistant

Temperature, humidity, atmospheric pressure and battery-voltage sensors for
your StratusDash devices. The integration polls the StratusDash cloud every
15 minutes. A StratusDash account with an owned device is required; shared
devices are excluded.

Version 0.2.0 adds quiet-hours history and controls. Deploy the matching
StratusDash history/control API before installing this version.

## Install with HACS

1. In HACS, open the three-dot menu, then **Custom repositories**.
2. Add `https://github.com/CasparN/home-assistant-stratusdash` with type
   **Integration**, then download **StratusDash**.
3. Restart Home Assistant.
4. Create a Home Assistant token in your StratusDash account settings.
5. In Home Assistant, open **Settings → Devices & services → Add integration**,
   choose **StratusDash**, and paste the token.

Requires Home Assistant 2026.7.2 or newer. This is a HACS custom repository;
it is not yet included in the default catalogue. For manual installation,
copy `custom_components/stratusdash` into your HA configuration directory.

If you previously installed the integration manually, replace that folder with
this version. Existing entity IDs and account configuration are preserved.
Rotating or revoking a token invalidates the old token immediately; the
integration will ask you to reauthenticate.

## Overnight graphs

Add a **Statistics graph** card to your HA dashboard. Select the StratusDash
statistics named for your device and measurement with a **history** suffix, choose an hourly period,
and enable minimum, maximum and mean. These are separate from the live sensor's
automatically generated statistics. Each sensor also exposes the correct
`history_statistic_id` attribute for use in YAML:

```yaml
type: statistics-graph
title: Overnight temperature
period: hour
days_to_show: 2
stat_types: [min, max, mean]
entities:
  - entity: stratusdash:YOUR_HISTORY_STATISTIC_ID
```

Replace the example ID with the sensor's full `history_statistic_id` value.
The first load imports up to seven days. Normal polling rechecks the previous
24 hours, so late overnight batches update existing hours without duplicates.
After a failed import, recovery can look back up to seven days. Reloading HA
also repeats the initial import safely.

Statistics use actual samples grouped by capture hour, with no invented values
for missing samples. Only finished hours are exported; a partially received
hour is corrected as further uploads arrive. Recovery may take multiple device
uploads plus the next 15-minute HA poll. Power loss or buffer overflow can lose
readings before upload; those cannot be recovered.

This fills the **Statistics graph**, not the recent raw-state **History graph**.
History imports do not change live sensor values or replay automation events.
Leave quiet hours off if you need fresh overnight readings for automations.
Live sensors become unavailable after 45 minutes without device contact;
their attributes show the last sample time and confirmed quiet-hours end.

## Controls

Each owned device has a quiet-hours switch, start/end time controls and a layout
dropdown. Times use the **device's timezone**, shown in the entity attributes.
Schedules must last 15 minutes to 12 hours and can cross midnight. If changing
one boundary would temporarily make the schedule invalid, change the other first.
Layout choices are the same as in StratusDash.

Controls save the requested setting in StratusDash and remain available while
the device sleeps. The device applies changes at its next connection; HA cannot
wake it remotely. Quiet hours may need another connection to arm. Disabling the
switch during an already-confirmed quiet period does not immediately end sleep.

Your existing token grants these controls as well as access to readings.
Revoking it in account settings removes both kinds of access.

## Development

Use Python 3.14, install `requirements-test.txt`, then run `pytest` and
`ruff check custom_components tests`. Issues and contributions belong in
this repository. The integration is MIT licensed.
