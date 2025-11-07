F1_FANTASY_RULES_2025 = {
    "team": {
        "budget_start": 100.0,  # in millions, $100 M at season start :contentReference[oaicite:1]{index=1}
        "drivers_per_team": 5,
        "constructors_per_team": 2,
        # transfers etc omitted for now, focus on scoring
    },
    "scoring": {
        "qualifying": {
            "driver": {
                "finish_points": {
                    1: 10, 2: 9, 3: 8, 4: 7, 5: 6, 6: 5, 7: 4, 8: 3, 9: 2, 10: 1
                },
                "finish_positions_11_to_20": 0,
                "no_time_set_NC": -5,       # Did not set a time (NC) :contentReference[oaicite:2]{index=2}
                "disqualified": -15        # Disqualified in qualifying :contentReference[oaicite:3]{index=3}
            },
            "constructor": {
                # Based on both drivers of constructor
                "neither_reaches_Q2": -1,
                "one_reaches_Q2": +1,
                "both_reach_Q2": +3,
                "one_reaches_Q3": +5,
                "both_reach_Q3": +10
                # Note: scoring text says “Combined total of its two drivers … plus” :contentReference[oaicite:4]{index=4}
            }
        },
        "sprint_race": {
            "driver": {
                "finish_points": {
                    1: 8, 2: 7, 3: 6, 4: 5, 5: 4, 6: 3, 7: 2, 8: 1
                },
                "9_to_20": 0,
                "DNF_or_not_classified": -20,
                "disqualified": -25
            }
            # Overtakes, positions gained etc not always published clearly per sprint (may follow GP rules)
        },
        "grand_prix": {
            "driver": {
                "finish_points": {
                    1: 25, 2: 18, 3: 15, 4: 12, 5: 10, 6: 8, 7: 6, 8: 4, 9: 2, 10: 1
                },
                "11_to_20": 0,
                "DNF_or_not_classified": -20,
                "disqualified": -25
            },
            "driver_bonus": {
                "positions_gained": +1,     # per net position gained vs qualifying position :contentReference[oaicite:5]{index=5}
                "positions_lost": -1,       # per net position lost :contentReference[oaicite:6]{index=6}
                "overtakes_made": +1,       # legal on-track overtakes only :contentReference[oaicite:7]{index=7}
                "fastest_lap": +10,         # awarded if driver sets fastest lap :contentReference[oaicite:8]{index=8}
                "driver_of_the_day": +10    # selected via vote on F1.com :contentReference[oaicite:9]{index=9}
            },
            "constructor": {
                # Based on performance of both drivers
                # Plus pit-stop performance bonus
                "pitstop_time_scoring": {
                    "over_3.0_sec": 0,
                    "2.5_to_2.99_sec": 2,
                    "2.2_to_2.49_sec": 5,
                    "2.0_to_2.19_sec": 10,
                    "under_2.0_sec": 20
                },
                "fastest_pitstop_bonus": +5,
                "world_record_pitstop_bonus": +15
                # from rule-summary :contentReference[oaicite:10]{index=10}
            }
        }
    },
    "transfers": {
        "free_transfers_per_round": 2,
        "carry_over_max": 1,
        "carry_over_condition": "If you use fewer than the free transfers in a race-week, you may carry over up to 1 unused transfer into the next race. The maximum free transfers for a given round would then be 3. :contentReference[oaicite:1]{index=1}",
        "exceeding_free_transfers": {
            "penalty_per_extra_transfer": -10,
            "comment": "Each additional transfer beyond the allowed/free number triggers a -10 point deduction. :contentReference[oaicite:2]{index=2}"
        },
        "special_cases": {
            "inactive_driver_swap_suggestion": {
                "description": "If a selected driver competes in the Sprint but is replaced before Qualifying for the Grand Prix, a transfer suggestion may be given. That suggestion still counts toward the transfer limit (and thus if exceeded will incur penalty). :contentReference[oaicite:3]{index=3}"
            },
            "chip_interactions": {
                "No_Negative_chip": {
                    "note": "The 10-point transfer penalty is **not negated** by the No Negative chip. :contentReference[oaicite:4]{index=4}"
                }
            }
        }
    },
    "chips": {
        # Power-ups / special modifiers
        "DRS_boost": {
            "normal": "select one driver to double their score for the weekend"  # from “How to Play” :contentReference[oaicite:11]{index=11}
        },
        "Extra_DRS": {
            "effect": "one driver’s score is tripled (in addition to normal DRS which still doubles another driver)", 
            "once_per_season": True
        },
        "No_Negative": {
            "effect": "if any driver or constructor scores negative in Sprint/Qualifying/GP, their negative is reset to zero",
            "once_per_season": True
        },
        "Auto_Pilot": {
            "effect": "automatically assign DRS boost to highest-scoring driver in your team that weekend",
            "once_per_season": True
        },
        "Wildcard": {
            "effect": "allows unlimited transfers for one raceweek while staying under budget",
            "once_per_season": True
        },
        "Limitless": {
            "effect": "unlimited budget for one raceweek",
            "once_per_season": True
        }
    },
    "penalties_and_misc": {
        "lineup_lock_deadline": "lineup locks at start of Qualifying (or Sprint weekends: start of Sprint race)" ,  # :contentReference[oaicite:12]{index=12}
        "late_team_creation_penalty": -10  # teams created after lineup lock incur 10 point penalty :contentReference[oaicite:13]{index=13}
    }
}
