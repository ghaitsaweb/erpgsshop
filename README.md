# ERP GSSHOP

Clean-room modular ERP for GSSHOP. The application uses Django, PostgreSQL, Redis, Celery, environment-based configuration, and Docker Compose. It does not copy HMX source code, assets, packages, or licensing components.

## Application suite

The Apps workspace at `/apps/` is the capability map and shows an honest maturity badge for every application:

- `stable`: working foundation already covered by automated tests.
- `beta`: working data model and CRUD workflow; deeper automation/reporting is still being expanded.
- `planned`: visible roadmap item and cannot be enabled yet.

Each application has its own landing workspace with record counters, operational menus, common workflow guidance, recent records, and related applications. Administrators can install applications with their dependencies or uninstall them without deleting business data. Applications required by another installed module cannot be removed until the dependent module is uninstalled.

Current working coverage includes:

- General ERP: multi-company master data, contacts, products, warehouses, and access control.
- Sales, purchase, invoicing, and an immutable stock ledger.
- CRM leads and opportunities.
- HR, contracts, attendance, leave, overtime, payroll, recruitment, and appraisal.
- Accounting foundation, journals, budgets, expenses, and loans.
- Bills of material, manufacturing orders, and quality inspections.
- Projects, tasks, assets, maintenance, and construction work packages.
- POS retail and F&B registers, sessions, orders, tables, and kitchen tickets.
- School, LMS enrollment/progress, and library lending.
- Governed AI coworker workspaces, agents, conversations, and asynchronous tasks.
- Localization packs for Indonesia, Singapore, Malaysia, Philippines, Thailand, and Australia.

Transactional records use a shared document experience: workflow buttons and status bar in the header, document fields, editable detail lines, totals, and status-based locking. It currently covers sales orders, purchase orders, invoices, journal entries, bills of material, POS orders, projects/tasks, construction/work packages, courses/lessons, and AI conversations/messages.

Advanced workflows such as comprehensive statutory reporting, tax filing connectors, production planning, assessments, omnichannel POS, and autonomous AI tool execution remain staged roadmap work. The application catalog labels them instead of representing them as complete.

## Start locally

Port `8010` is the default. Copy and customize `.env.example` before production use.

```bash
./gsshop runserver
./gsshop status
```

Open <http://localhost:8010>. The local bootstrap account is configured in `.env`.

For document line items and detailed configuration, use `/admin/`. The main workspace supports list, search, create, edit, and controlled deletion of module records.

## Familiar developer workflow

```bash
./gsshop migrate
./gsshop shell
./gsshop test
./gsshop logs
```

The Compose stack contains `web`, `db`, `redis`, and `worker` services. Database and Redis data use named volumes and are not removed by ordinary rebuilds.

This deliberately follows familiar Django/HMX-style environment and management conventions while keeping the implementation independent.

## Add-on development

New modules can be discovered without editing the built-in catalog. Create
`addons/<module>/manifest.json` using `addons/_template/manifest.json.example`,
then reload the application environment:

```bash
./gsshop reload-apps
```

The manifest can declare metadata, dependencies, a Django `AppConfig`, and a
URL configuration. On reload, migrations run, the catalog is synchronized, and
the new module appears in Apps ready for an administrator to install. Use
`./gsshop sync-apps` when only manifest metadata changed and no Python code or
environment variable needs reloading.

## Production checklist

- Replace all passwords and `DJANGO_SECRET_KEY` in `.env`.
- Set `SEED_DEMO=False`.
- Set the exact public host and CSRF origins.
- Put the app behind HTTPS and a reverse proxy.
- Configure database backups and monitoring.
- Add the product licensing/subscription module only after the ERP workflows are stable.

## GitHub and deployment on a new server

Never put a GitHub token, password, private key, or the real `.env` file in this
repository. GitHub does not accept an account password for Git operations. Use a
separate SSH key on each workstation or server instead.

### 1. Connect a machine to GitHub with SSH

Run these commands as the normal Linux user that will run Git, not as `root`:

```bash
ssh-keygen -t ed25519 -C "yusuffkurniawan91@gmail.com"
eval "$(ssh-agent -s)"
ssh-add ~/.ssh/id_ed25519
cat ~/.ssh/id_ed25519.pub
```

Copy only the displayed **public** key to GitHub under **Settings > SSH and GPG
keys > New SSH key**. Never copy `~/.ssh/id_ed25519`, because that is the private
key. Test the connection:

```bash
ssh -T git@github.com
```

Repeat this step on every new server with a new key. A production server that
only needs to deploy should preferably use a read-only deploy key. Give write
access only to machines that really need to push.

### 2. Publish this existing project for the first time

Create an empty **private** repository named `erp-gsshop` in the intended GitHub
account. Do not initialize it with a README, license, or `.gitignore`. Then run:

```bash
cd /home/yusufmjt/erp-gsshop
git init -b main
git config user.name "Yusuf Kurniawan"
git config user.email "yusuffkurniawan91@gmail.com"
git add .
git status
git commit -m "Initial ERP GSSHOP import"
git remote add origin git@github.com:GITHUB_USERNAME/erp-gsshop.git
git push -u origin main
```

Replace `GITHUB_USERNAME` with the GitHub username shown in the account URL; an
email address is not the repository owner name.

### 3. Clone and start on a new server

Install Git, Docker Engine, and the Docker Compose plugin first. Then:

```bash
git clone git@github.com:GITHUB_USERNAME/erp-gsshop.git
cd erp-gsshop
cp .env.example .env
chmod 600 .env
```

Edit `.env` and replace every `change-this-...` value. Generate strong random
values with `openssl rand -base64 48`. For a production server, also set
`DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`, and `SEED_DEMO=False`.
Validate and start the stack:

```bash
docker compose config --quiet
./gsshop runserver
./gsshop status
curl --fail http://127.0.0.1:8010/health/
```

The first start builds the image, waits for PostgreSQL and Redis, applies Django
migrations, collects static files, and creates the configured admin account.
The database and Redis state live in named Docker volumes and are not stored in
Git. Back them up separately before production upgrades.

### 4. Daily pull and deploy

Check local changes before pulling. `--ff-only` prevents Git from silently
creating a merge commit:

```bash
cd erp-gsshop
git status --short
git pull --ff-only
docker compose up -d --build
docker compose ps
curl --fail http://127.0.0.1:8010/health/
```

### 5. Daily commit and push

```bash
cd erp-gsshop
git status
git add path/to/changed-file
git diff --cached
git commit -m "Describe the change"
git push
```

Do not use `git add .` casually after the initial import. Stage explicit paths,
review the staged diff, and verify that `.env`, database dumps, logs, uploaded
media, backups, and private keys are not included.
