# Rutscout

Rutscout is a tool for testing and automatically switching between SIMs on a Teltonika RUTX50 router. It may work for other models as well, but it has only been tested on the RUTX50.

Rutscout cycles through the SIMs in the router, runs a speed test on each one, and switches to the fastest SIM. It uses the [Ponika](https://github.com/bencevans/ponika) library to interact with the router's API.


## Usage

Set your router's IP address and credentials as environment variables:

```bash
export RUTSCOUT_ROUTER_IP=192.168.1.1
export RUTSCOUT_ROUTER_USERNAME=admin
export RUTSCOUT_ROUTER_PASSWORD=password
```

Then, run the tool:

```bash
uvx rutscout
```

## Development

1. Clone the repository:

   ```bash
   git clone https://github.com/bencevans/rutscout.git
   cd rutscout
   ```

2. Install the required dependencies:

   ```bash
   uv sync
   ```

3. Set the environment variables for your router's IP address and credentials:

   ```bash
   export RUTSCOUT_ROUTER_IP=
   export RUTSCOUT_ROUTER_USERNAME=
   export RUTSCOUT_ROUTER_PASSWORD=
   ```

4. Run the tool:

   ```bash
   uv run rutscout
   ```

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.