# Rails delivery evidence

Use repo.read_file or repo.search_text to inspect the pinned Gemfile.lock, config/puma.rb, config/database.yml, config/environments/production.rb, config/routes.rb, Rakefile and at least one versioned migration. These content reads and the skill hash form the managed pilot's review record. Inspect the health controller as evidence of readiness. Do not execute source or read encrypted credentials, keys or environment files.

Corroborate a database requirement with both the locked adapter and production connection settings. Cite the actual required environment-variable references and supply names only. Distinguish development and test settings from production. A database gem by itself does not establish a production dependency.

Cite the production launcher, its locked dependencies, the external listen binding, the port source and the routes. Distinguish liveness from readiness and trace readiness to the application controller. Cite a native migration declaration and source-owned migration files. Treat unclear or conflicting alternatives as requiring review. Do not claim migration safety from a command or directory name alone.

Use structured command candidates with argument lists and evidence IDs when available. Keep credential creation and deployment out of the analysis. Backend policy decides whether the deployment target can satisfy a requirement.
