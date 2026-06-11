from os import getenv
from sys import exit, stderr
from time import sleep, time
from typing import Any, Dict, List, Optional, TypeVar

from ponika import PonikaClient
from ponika.endpoints.modems import ModemsEndpoint
from ponika.models import ApiResponse
from rich import print
from speedtest import Speedtest


def create_client() -> PonikaClient:
    ROUTER_IP = getenv("RUTSCOUT_ROUTER_IP", None)
    ROUTER_USERNAME = getenv("RUTSCOUT_ROUTER_USERNAME", None)
    ROUTER_PASSWORD = getenv("RUTSCOUT_ROUTER_PASSWORD", None)

    if not ROUTER_IP or not ROUTER_USERNAME or not ROUTER_PASSWORD:
        print(
            "Please set the RUTSCOUT_ROUTER_IP, RUTSCOUT_ROUTER_USERNAME, and RUTSCOUT_ROUTER_PASSWORD environment variables.",
            file=stderr,
        )
        exit(1)

    # Suppress TLS verification warnings since we're connecting to a local device
    from urllib3.exceptions import InsecureRequestWarning
    from urllib3 import disable_warnings

    disable_warnings(InsecureRequestWarning)

    return PonikaClient(
        host=ROUTER_IP,
        username=ROUTER_USERNAME,
        password=ROUTER_PASSWORD,
        verify_tls=False,
    )


def print_modem_state(
    state: ModemsEndpoint.GetModemsStatusOnline | ModemsEndpoint.GetModemsStatusOffline,
) -> None:
    if isinstance(state, ModemsEndpoint.GetModemsStatusOnline):
        print(
            f"- [magenta]{state.id}[/magenta]: [gray]{state.provider}[/gray] [green]{state.conntype}[/green]"
        )
        print(
            f"  RSSI: [yellow]{state.rssi}[/yellow] dBm   RSRP: [yellow]{state.rsrp}[/yellow] dBm   RSRQ: [yellow]{state.rsrq}[/yellow] dB"
        )
    else:
        print("[red]Modem is offline[/red]")


def speedtest() -> Optional[Dict[str, float]]:
    print("  [blue]Running speedtest...[/blue]")
    try:
        st = Speedtest()
        st.get_best_server()
        st.download()
        st.upload(pre_allocate=False)
        results = st.results.dict()
        download = float(results["download"]) / 1_000_000
        upload = float(results["upload"]) / 1_000_000
        ping = float(results["ping"])

        print(f"  Download: [green]{download:.2f} Mbps[/green]")
        print(f"  Upload: [green]{upload:.2f} Mbps[/green]")
        print(f"  Ping: [green]{ping:.1f} ms[/green]")

        return {"download": download, "upload": upload, "ping": ping}
    except Exception as exc:
        print(f"[red]Speedtest failed: {exc}[/red]")
        return None


T = TypeVar("T")


def unwrap_response(response: ApiResponse[T]) -> T:
    if response.success and response.data is not None:
        return response.data
    else:
        print(f"[red]API request failed: {response.errors}[/red]", file=stderr)
        exit(1)


def set_primary(router: PonikaClient, sim_card_id: str) -> None:
    from ponika.endpoints.sim_cards import SimCardsEndpoint

    payload = {"data": {"primary": "1"}}
    # print(f"[blue]Attempting set_primary: /api/sim_cards/config/{sim_card_id}[/blue]")

    # Try high-level client first
    try:
        router._put(
            f"/sim_cards/config/{sim_card_id}",
            SimCardsEndpoint.GetSimCardConfig,
            payload,
        )
    except Exception:
        # suppressed debug exception details
        pass

    # Verify via GET whether the SIM became primary
    try:
        sim_cards = unwrap_response(router.sim_cards.get_config())
        sim = next((s for s in sim_cards if s.id == sim_card_id), None)
        if sim and getattr(sim, "primary", "0") == "1":
            # print(f"  [green]Verified primary on {sim_card_id} via API.[/green]")
            return
    except Exception as exc:
        print(f"  [yellow]Verification GET failed: {exc}[/yellow]")

    # Fallback: send raw HTTP PUT using the client's session (mirrors browser)
    try:
        session = router._request
        endpoint = f"/api/sim_cards/config/{sim_card_id}"
        url = f"{router._config.base_url}{endpoint}"
        headers = {"Content-Type": "application/json"}
        # Try without CSRF header first (UI includes it, but API may accept without)
        session.put(url, json=payload, headers=headers, verify=router._config.verify_tls, cookies=session.cookies)
    except Exception:
        # suppressed debug exception details
        pass

    # Final verification
    try:
        sim_cards = unwrap_response(router.sim_cards.get_config())
        sim = next((s for s in sim_cards if s.id == sim_card_id), None)
        if sim and getattr(sim, "primary", "0") == "1":
            print(f"  [green]Verified primary on {sim_card_id} after attempts.[/green]")
            return
        else:
            print(f"  [red]SIM {sim_card_id} is not primary after attempts.[/red]")
    except Exception as exc:
        print(f"  [red]Final verification GET failed: {exc}[/red]")


def is_connected(modem: Any) -> bool:
    state = str(getattr(modem, "data_conn_state", "")).lower()
    if not state:
        return False
    if state in {"down", "disconnected", "no service", "off", "0"}:
        return False
    return True


def wait_for_connection(
    router: PonikaClient,
    modem_id: str,
    sim_position: str,
    timeout: int = 120,
    poll_interval: int = 5,
) -> Optional[Dict[str, Any]]:
    print(f"Waiting for modem {modem_id} to select SIM {sim_position} and connect...")
    deadline = time() + timeout
    while time() < deadline:
        modem_status = unwrap_response(router.modems.get_status())
        modem = next(
            (m for m in modem_status if str(getattr(m, "id", "")) == str(modem_id)),
            None,
        )
        if modem is None:
            print("  [yellow]No modem status returned yet.[/yellow]")
            sleep(poll_interval)
            continue

        active_sim = str(getattr(modem, "active_sim", ""))
        data_state = str(getattr(modem, "data_conn_state", "")).lower()

        if active_sim == str(sim_position) and is_connected(modem):
            print("  [green]SIM is active and connected.[/green]")
            return modem.__dict__ if hasattr(modem, "__dict__") else dict(modem)

        sleep(poll_interval)

    print(f"[red]Timed out waiting for SIM {sim_position} on modem {modem_id} to connect.[/red]")
    return None


def print_sim_summary(
    sim_cards: list,
    modem_status: list[ModemsEndpoint.GetModemsStatusOnline | ModemsEndpoint.GetModemsStatusOffline],
) -> None:
    modem_map = {str(modem.id): modem for modem in modem_status}
    print("Router SIM / modem summary")
    for sim in sim_cards:
        modem = modem_map.get(str(sim.modem))
        status = "offline"
        active = "no"
        if modem is not None:
            active = "yes" if str(getattr(modem, "active_sim", "")) == str(sim.position) else "no"
            status = getattr(modem, "data_conn_state", "unknown") if isinstance(
                modem, ModemsEndpoint.GetModemsStatusOnline
            ) else "offline"

        print(
            f"- SIM {sim.position} on modem {sim.modem}: operator={sim.operator} service={sim.service} primary={sim.primary} active={active} status={status}"
        )


def select_best_sim_for_modem(
    router: PonikaClient,
    modem_id: str,
    sim_cards: list,
    current_modem: ModemsEndpoint.GetModemsStatusOnline | ModemsEndpoint.GetModemsStatusOffline,
) -> None:
    sims = [sim for sim in sim_cards if str(sim.modem) == str(modem_id)]
    if not sims:
        return

    print(f"\nChecking modem {modem_id} with {len(sims)} SIM(s)")
    if isinstance(current_modem, ModemsEndpoint.GetModemsStatusOnline):
        print_modem_state(current_modem)
        print(f"  Current active SIM: [cyan]{current_modem.active_sim}[/cyan]")
    else:
        print("  [yellow]Modem is offline. SIM selection will still be attempted.[/yellow]")

    results = []
    for sim in sims:
        print(f"\nTesting SIM {sim.position} (operator={sim.operator}, primary={sim.primary})")
        if not isinstance(current_modem, ModemsEndpoint.GetModemsStatusOnline) or str(current_modem.active_sim) != str(sim.position):
            set_primary(router, sim.id)
            modem = wait_for_connection(router, modem_id, sim.position)
            if modem is None:
                results.append({"sim": sim, "speed": None})
                continue
        else:
            modem = current_modem
            if not is_connected(modem):
                modem = wait_for_connection(router, modem_id, sim.position)
                if modem is None:
                    results.append({"sim": sim, "speed": None})
                    continue

        # Print connection type and signal strengths for this modem before speedtest
        def _mget(m, key):
            return m.get(key) if isinstance(m, dict) else getattr(m, key, None)

        conntype = _mget(modem, "conntype") or _mget(modem, "conn_type") or "unknown"
        rssi = _mget(modem, "rssi")
        rsrp = _mget(modem, "rsrp")
        rsrq = _mget(modem, "rsrq")
        sig_parts = []
        if rssi is not None:
            sig_parts.append(f"RSSI: {rssi} dBm")
        if rsrp is not None:
            sig_parts.append(f"RSRP: {rsrp} dBm")
        if rsrq is not None:
            sig_parts.append(f"RSRQ: {rsrq} dB")
        sig_desc = "   ".join(sig_parts) if sig_parts else ""
        print(f"  Connection: [cyan]{conntype}[/cyan] {sig_desc}")

        speed = speedtest()
        results.append({"sim": sim, "speed": speed})

    available = [entry for entry in results if entry["speed"] is not None]
    if not available:
        print(f"[red]No usable speeds measured for modem {modem_id}. Keeping current SIM selection.[/red]")
        return

    best = max(
        available,
        key=lambda entry: (entry["speed"]["download"], -entry["speed"]["ping"]),
    )
    best_sim = best["sim"]

    print(
        f"\nBest SIM for modem {modem_id}: position={best_sim.position} operator={best_sim.operator} download={best['speed']['download']:.2f} Mbps upload={best['speed']['upload']:.2f} Mbps ping={best['speed']['ping']:.1f} ms"
    )

    # Re-fetch current state to avoid stale `sim.primary` values
    try:
        current_modem_status = unwrap_response(router.modems.get_status())
        current_modem = next(
            (m for m in current_modem_status if str(getattr(m, "id", "")) == str(modem_id)),
            None,
        )
        active_sim_now = str(getattr(current_modem, "active_sim", "")) if current_modem is not None else ""
    except Exception:
        active_sim_now = ""

    try:
        fresh_sims = unwrap_response(router.sim_cards.get_config())
        fresh_best = next((s for s in fresh_sims if s.id == best_sim.id), None)
        fresh_primary = getattr(fresh_best, "primary", "0") if fresh_best is not None else "0"
    except Exception:
        fresh_primary = "0"

    needs_set = (fresh_primary != "1") or (str(active_sim_now) != str(best_sim.position))

    if needs_set:
        print(f"Setting SIM {best_sim.position} as primary for modem {modem_id}...")
        set_primary(router, best_sim.id)
    else:
        print(f"SIM {best_sim.position} is already configured and active for modem {modem_id}.")


def main() -> None:
    router = create_client()

    print("Checking SIM card configuration...")
    sim_cards = unwrap_response(router.sim_cards.get_config())
    print(
        f"Found {len(sim_cards)} SIM cards on {len(set(str(card.modem) for card in sim_cards))} modems."
    )
    print()

    modem_status = unwrap_response(router.modems.get_status())
    print_sim_summary(sim_cards, modem_status)

    for modem in modem_status:
        select_best_sim_for_modem(router, str(modem.id), sim_cards, modem)


if __name__ == "__main__":
    main()
