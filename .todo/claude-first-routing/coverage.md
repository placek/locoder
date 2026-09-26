# Coverage: claude-first-routing

Status: verified 2026-09-26

Every acceptance criterion and spec decision, mapped to the test that proves it (tests in
`tests/test_routing.py` unless marked `report:` or `bakeoff:`) or to what was checked by
hand. `make test`: 75 passed. **Live** marks what can only be proven on alpha.

Checked against the pinned Hermes (`7b761da`) installed in a scratch container:
- `make check-offline`: the profile and plugin sections pass. Its only failures there
  were defuddle and the sandbox image, which that container lacked.
- `skills list` shows `routing-mode` enabled.
- Hermes' tool registry passes `session_id` to the handlers.
- `hermes_state.SessionDB` resolves a twice-rotated session to its lineage root.
- With `LOCODER_ROUTING_CHILD=1`, the routing tools refuse when dispatched through Hermes.
- The CLI accepts the OpenRouter rung's command.

## Tickets

### 01 · Claude-first routing decision
| criterion | evidence |
|---|---|
| near-certain → coder, claude, claude | `test_near_certain_task_starts_on_coder_then_claude_with_one_retry` |
| P 0.7 → claude, claude | `test_merely_likely_task_starts_on_claude`, `test_confident_judge_but_not_trivial_task_starts_on_claude` |
| locked out: P 0.4 → coder, openrouter; P 0.2 → openrouter | `test_locked_out_with_fair_chance_tries_coder_then_openrouter`, `test_locked_out_and_unlikely_goes_straight_to_openrouter` |
| judge unreachable → claude | `test_judge_down_starts_on_claude`, `test_route_with_judge_down_still_decides`, `test_judge_down_while_locked_out_is_treated_as_pessimistic` |
| no pace, allowance or weekly budget left | grep of code, config and docs (only a migration fixture names the old `pace` column) |
| thresholds from routing.yaml, next call | `test_thresholds_come_from_config`, `test_settings_merge_routing_yaml`; config is re-read per call |
| `routing_status` reports availability and the week | `test_lockout_lasts_until_the_stated_reset_and_no_longer`, `test_routing_status_reports_this_weeks_results` |
| make test / check-offline | 75 passed; check-offline profile and plugin sections pass on pinned Hermes; **live** for host tools |

### 02 · Lockout until the stated reset
| criterion | evidence |
|---|---|
| locked out until the stated reset, no longer | `test_a_session_limit_locks_out_for_hours_not_the_week` (3 h, both sides of the reset), `test_lockout_lasts_until_the_stated_reset_and_no_longer` |
| clock, date, relative (and epoch) parsed | `test_reset_time_reads_clock_date_and_relative_forms`, `test_reset_time_honours_a_timezone_named_in_the_error`, `test_reset_time_unreadable_is_none` |
| unreadable reset → fallback lockout | `test_unreadable_reset_locks_out_for_the_fallback_period_and_keeps_the_text` |
| raw text stored | same, and `test_stated_reset_is_recorded_with_its_text` |
| mid-run hit → rest of chain, no OpenRouter run | `test_limit_hit_mid_run_hands_back_the_locked_out_chain`, `test_escalate_without_a_decision_treats_the_rest_as_pessimistic`, `test_lockout_chain_skips_a_rung_this_task_already_failed` |
| real `-p` limit wording | **live**: check `limit_hits.raw` after the first real hit |

### 03 · Record the commit
| criterion | evidence |
|---|---|
| HEAD and dirty flag stored | `test_route_records_the_commit_and_dirty_state_of_a_git_workdir` |
| no workdir or non-git → nothing stored | `test_route_without_git_records_no_commit` |
| old ledgers migrate | `test_old_ledger_gains_claude_column_and_keeps_rows` |

### 04 · Spike: Hermes child on OpenRouter
| criterion | evidence |
|---|---|
| mechanism shown working | command accepted by the pinned CLI; **live** for a run that reaches OpenRouter |
| where cost comes from | ticket 04 findings; `test_parse_oneshot_reads_usage_and_exit_code`, `test_parse_oneshot_reads_a_real_failed_usage_file` |
| tools under focus | ticket 04 findings, revised: the child gets `file,terminal,web,todo` |

### 05 · OpenRouter step as a Hermes child
| criterion | evidence |
|---|---|
| runs as a one-shot Hermes run on the configured model | `test_openrouter_command_runs_this_profile_one_shot`, `test_openrouter_rung_runs_hermes_one_shot_and_records_its_cost`; **live** for the sandbox |
| the child cannot re-delegate or escalate | `test_openrouter_env_keeps_the_profile_env_and_marks_the_child`, `test_routing_tools_refuse_inside_the_openrouter_child`; refusal also shown through Hermes' dispatcher |
| cost recorded | same, and `test_failed_openrouter_run_still_records_its_cost` |
| nothing launches `claude` at OpenRouter | `test_openrouter_command_runs_this_profile_one_shot`, `test_subscription_env_strips_api_billing_vars_but_keeps_the_login_dir` |

### 06 · Bake-off
| criterion | evidence |
|---|---|
| passes, cost and time per model | bakeoff: `test_bakeoff_scores_each_model_and_leaves_no_trace` |
| worktree at the recorded commit, removed after | same |
| skipped tasks counted | same |
| ledger unchanged | same (ledger hash before and after); the child can no longer write to it |
| stray changes fail, backticked or plain paths | bakeoff: `test_brief_parsing`, `test_bakeoff_scores_each_model_and_leaves_no_trace` (the `sprawl` model) |
| picking the model | **live**: needs about 10 clean routed tasks |

### 07 · Report
| criterion | evidence |
|---|---|
| each figure correct on a seeded ledger | report: `test_report_answers_the_goals` (days without Claude Code, weekly verified rate and spend, monthly spend, per-rung pass rate and cost, the three coder groups, calibration bands, limit hits) |
| fallback and sure-thing counted apart | same; fallback is read from the decision's snapshot or the lockout window |
| no pace or budget | same |
| overlapping lockouts not double-counted | report: `test_overlapping_limit_hits_are_not_double_counted` |

### 08 · Routing modes
| criterion | evidence |
|---|---|
| new session auto; local forces the coder | `test_sessions_start_in_auto_and_switch_independently`, `test_local_mode_forces_the_coder_with_no_retry`, `test_local_mode_stays_local_while_claude_is_locked_out` |
| claude mode: claude, claude; locked out → user and reset | `test_claude_mode_forces_claude_with_its_retry`, `test_claude_mode_while_locked_out_hands_back_with_the_reset` |
| claude mode mid-run hit → reset, no chain | `test_claude_mode_hands_back_while_locked_out` |
| `escalate()` honours the mode | `test_escalate_refuses_paid_rungs_in_local_mode`, `test_escalate_in_claude_mode_never_runs_openrouter` |
| sessions independent; mode survives compression | `test_sessions_start_in_auto_and_switch_independently`, `test_mode_survives_compression_rotating_the_session_id`; the root lookup was checked on a real Hermes session DB |
| forced decisions record verdict and mode | `test_forced_decisions_still_record_the_verdict_and_the_mode` |
| `routing_status` shows the mode; unknown mode refused | `test_sessions_start_in_auto_and_switch_independently`, `test_unknown_mode_is_refused` |
| skill discovered | `skills list` on pinned Hermes |

## Gaps found by the audit, all fixed

1. **The OpenRouter child could re-delegate.** It got `-t coding` (which has `delegate_task`), `SOUL.md` and the auto-loaded `delegate` skill. It now gets `-t file,terminal,web,todo`, a worker preamble and `LOCODER_ROUTING_CHILD=1`, and the plugin refuses when that is set.
2. **The routing mode was lost on compression.** Hermes rotates the session id when it compresses. Modes are now keyed by the conversation's lineage root.
3. **A mid-run limit hit could re-run a coder that had already failed.** Rungs this decision has already failed are now dropped from the handed-back chain.
4. **`escalate()` ignored the mode.** It now refuses both paid rungs in `local`, and OpenRouter in `claude`. In `claude` mode while Claude Code is locked out, it hands the task back.
5. **The bake-off's stray-change check missed paths written without backticks.** Plain paths in the brief are now parsed too.
6. **The report classified fallbacks by timestamp alone, and grouped months by host time.** It now also reads the decision's snapshot, and uses the configured timezone.
7. **Leftovers from the old design:**
   - `config.yaml` listed four routing tools.
   - `CLAUDE_CONFIG_DIR` was still stripped from the Max rung.
   - `limit_hits.spent` is still recorded: it is Claude Code spend at the hit, not a budget, so it stays.
8. **Docs and tests:** the `delegate` skill's mode text and the README's limit and mode sections are updated, and the missing assertions have been added.
