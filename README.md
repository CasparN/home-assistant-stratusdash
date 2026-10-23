# StratusDash for Home Assistant

Temperature, humidity, atmospheric pressure and battery-voltage sensors for
your StratusDash devices. The integration polls the StratusDash cloud every
15 minutes. A StratusDash account with an owned device is required; shared
devices are excluded.

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

## Development

Use Python 3.14, install `requirements-test.txt`, then run `pytest` and
`ruff check custom_components tests`. Issues and contributions belong in
this repository. The integration is MIT licensed.
