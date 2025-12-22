# NERSC

## Login

Simple ssh like in the example below requires to to enter your password **and** authentificator CODE every time:

```bash
ssh <user-name>@perlmutter.nersc.gov
```

### sshproxy

For instructions see [docs.nersc.gov/connect/mfa/#sshproxy](https://docs.nersc.gov/connect/mfa/#sshproxy)
The `sshproxy` allows to generate certificates that can be used for 24h:

1. Get `sshproxy` for your laptop/machine from [portal.nersc.gov/cfs/mfa/](https://portal.nersc.gov/cfs/mfa/)

2. Run:
```bash
sshproxy -u <user-name>
```

3. Add the following to your `.ssh/config`:

```
Host perlmutter*.nersc.gov saul*.nersc.gov dtn*.nersc.gov
    User <user-name>
    IdentityFile ~/.ssh/nersc
    IdentitiesOnly yes
    ForwardAgent yes
```

## Quick Setup Script

The `setup_nersc.sh` script automates the setup of kb-mcp on NERSC systems:

1. **Download the script:**
   ```bash
   curl -O https://raw.githubusercontent.com/HEP-KE/kb-mcp/sld/scripts/setup_nersc.sh
   ```

2. **Source the script** (it must be sourced, not executed):
   ```bash
   source setup_nersc.sh
   ```

3. **Update the repository** (optional):
   ```bash
   source setup_nersc.sh --update
   ```

The script will:
- Create a virtual environment in `/global/common/software/` (persistent across sessions)
- Clone the repository to `$SCRATCH/kb-mcp`
- Link persistent data directory from CFS
- Install dependencies
- Load your `.env` from `~/.kb-mcp.env` or shared secrets

**Note:** The script must be sourced (not executed) because it sets up your environment variables.