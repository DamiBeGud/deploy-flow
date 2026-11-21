# python source investigation

Read dependency/build metadata, application entrypoints, tests, HTTP routes and configuration as text. Never execute/import source, wrappers, migrations, dependency hooks or shell commands. Do not read sensitive files, including any .env variant, keys or encrypted credentials.

Distinguish declared constraints from selected toolchains. Report supported findings with evidence IDs: runtime, runtime.version, runtime.package_manager, build.source_root, build.install_command, workflow.test_command, workflow.build_command, runtime.command, network.port and health_check. Inspect full route prefixes and listen bindings; a framework import alone does not prove a working app. An optional database driver does not prove a required service.

If existing source defines migrations or a required worker, report migration.command and workflow.worker_command. Do not invent SQL, credentials, tests, health routes or configuration values. Report migration.replay_safe only if inspected source supports bounded replay without resets/destructive operations; otherwise retain uncertainty. Hidden reference answers are not inputs. Keep missing/ambiguous commands, versions, state and dependency mappings explicit.

For delivery commands distinguish deployment from developer convenience. Corroborate an explicit production launcher with its installed requirements and imported application target. Record each command as its arguments when the draft schema exposes command_candidates.
