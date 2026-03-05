Two days before the first F1 race of 2025, we wrote an article on the potential, new price change algorithm for 2025 based on dummy data left in the game at launch by the developers. In it, we highlighted 2 main issues with this algorithm: race 1 and 2 being different from the rest & the average PPM thresholds being too low.

Two weeks later, the algorithm has seemingly been adjusted to remedy both of these issues. In this post we’ll discuss:

what we think is currently the most likely new algorithm (+ some possible alternatives)

where we feel it could be improved even further

and what the implications on strategy are.

Disclaimer: F1 Fantasy doesn’t provide concrete information on how their price change algorithm works, so this article is based on assumptions made from the data from the first 2 races. Therefore, some things could be inaccurate or could be changed later by F1 Fantasy. Use this info at your own risk!

1. The Price Tier threshold is not 20, but between 18.4 and 19
   There are still 2 price tiers in 2025, but it turns out that the threshold between them is not 20. Based on the dummy data, the threshold could have been between 18.4 (ANT, R1) and 20.4 (RUS, R3), and because F1 Fantasy had been consistently using round numbers for price tier thresholds in the past, we assumed too quickly it would be 20 this time around. Turns out that this was not the case, since ANT just got an A-Tier price increase for the Chinese GP at 19M.

For now, we’ll be assuming 19M is the threshold in our tools, but it could also be up to 0.5M lower. More data is needed to verify.

Here is the updated Asset Tier table:

| Performance | A Tier >=19M | B Tier <19M |
|-------------|--------------|-------------|
| Great       | +0.3         | +0.6        |
| Good        | +0.1         | +0.2        |
| Poor        | -0.1         | -0.2        |
| Terrible    | -0.3         | -0.6        |

2. The most likely new algorithm
   At its simplest, the algorithm still is: “Calculate the average points per million (AvgPPM) per asset and see in what performance category it falls.”

But this time, there are no exceptions for race 1 and 2 (no empty races added when calculating the average) and the thresholds are increased from 0.2, 0.3 and 0.4 to 0.6, 0.9 and 1.2 as shown in the image below.

There are some possible alternatives listed in section 6, but this is the algorithm that’s currently implemented in all of our tools.

| AvgPPM         | Performance    |
|----------------|----------------|
| 1.2 < x        | Great          |
| 0.9 < x < 1.2  | Good           |
| 0.6 < x < 0.9  | Poor           |
| x < 0.6        | Terrible       |

3. The remaining issues
   While it’s great news that this change solves the 2 main issues we discussed before the start of the season, there is still some definite room for improvement.

1) All races are equally important when calculating the average PPM

Currently, they’re using a non-weighted average PPM, meaning that the next race is equally important as the previous race and the one before that. Worded differently: the information players can use to find picks that will positively impact their budget, comes only 33% from predicting the next race and 66% from looking at what has already happened (or 50/50 in race 2). That is not a good distribution for a fantasy sports game.

Finding the ‘optimal’ race weights would require some more analyzing/simulating, but intuitively, a 4/7 , 2/7 , 1/7 split where the importance halves every passing race feels like a decent starting point. This way, what happened in the past 2 races still influences the odds of assets getting certain price changes, but the most important element is still predicting what will happen next race.

2) There are significant PPM differences between asset types (driver vs constructor) and asset tiers (A-Tier vs B-Tier)

Let’s look at the average PPM numbers of 2024*:

Drivers

A-Tier (≥19M): 0.985

B-Tier (<19M): 0.388

Combined: 0.632

Constructors:

A-Tier (≥19M): 2.310

B-Tier (<19M): 1.128

Combined: 1.601

(We’ve left out the differences between sprint and normal weekends to keep things simpler)

The new price change system at its core is: "increase in value if the asset performs better than [price * 0.9] and decrease it otherwise". So for the prices to be ‘balanced’, assets all need to get to an average PPM of 0.9. A-Tier drivers and B-Tier constructors are already pretty close, but B-Tier drivers and A-Tier constructors are very far off and will respectively go systematically down or up in price throughout the season. This can be improved in 3 possible ways:

Different starting prices. Lower for B-Tier drivers and higher for A-Tier constructors.

Changes to the points system so that B-Tier drivers earn more and A-Tier constructors earn less.

Different price change AvgPPM thresholds for each of these types (which would solve the imbalance in price changes, but not in points for budget spent).

4. A simulation of the 2024 season using the 2025 price change algorithm
   A great way to estimate the consequences of this new model is by simulating what would have happened to the prices over the 2024 season if we used this model. Luckily for us, the amazing @jonnymoomoomoo on Discord (who was also the first to find the new adjusted formula) did exactly that and shared his insights from it. We’ll share the most relevant ones here, but you can see all of them plus the full Excel file here on Discord.

The top 8 drivers (who were A-Tier for most of the races), gained 2.3M on average over the whole season - with only PER ending the season with a price lower than his starting price.

The other drivers (all B-Tier the whole season long) instead lost 2.3M on average with only MAG, OCO and ZHO ending positive over the whole season.

The A-Tier constructors almost always gained +0.3M. Only 6 out of 96 changes were different and only one was negative.

The B-Tier constructors also increased in price significantly (+ 3.5M on average), but still a lot less than the A-Tiers who gained 6.9M on average.

71% of the time, assets fell either in the "Great" or "Terrible" class.

Streaks of both losing budget and gaining budget are common. If an asset starts gaining value, it’s likely they’ll keep gaining for some amount of consecutive races before switching to a decrease. After which they’ll also keep losing for some amount of consecutive races until the cycle continues.



5. The implications on strategy
   Based on the insights gathered in section 4, these are the biggest takeaways for budget building:

A-Tier assets are the most consistent and predictable gainers over the season as a whole, BUT by jumping between the correct B-Tier assets to catch their multi-race +0.6M streaks, it’s possible to gain more budget overall.

Concretely:

Try to ride the +0.6M waves and ideally get off right before (or more realistically, right after) they have a bad race, because then they’re likely to lose budget in the next races as well.

Flexibility in budgets to move between decreasing an increasing B-Tier assets will be crucial. You don’t want to be stuck with the cheapest B-Tier assets without extra budget to get them out when they DNF.

Avoiding DNFs for B-Tier drivers will be even more important, because the budget swing is now 1.2M (from 0.6 to -0.6) instead of last year’s most common 0.7M swing between 0.5 and -0.2.

The difference in budget between casual players and players using this info (via for example our Budget Builder) will become very large, very fast.

But what about points?

Calculating how much budget gains are worth in points over the rest of the season is a hard problem to solve with many variables playing a role. Getting a definitive, correct answer is likely impossible, but a good ballpark estimate should already help a lot. We’re currently working on a feature in the Team Calculator where you’ll be able to input that number yourself and get the best teams optimized for the expected value not only for the next race, but for all later races as well.

And what about new assets coming into the game or missing a race?

We don’t have any information yet on how F1 Fantasy will handle these edge cases, so we’ll need to wait and see. Luckily, with TSU and LAW already swapping places after 2 races, we won’t have to wait long!

6. Alternative - but less likely - algorithms
   Alternative 1:

The dummy data algorithm actually hasn’t changed, but they simply increased the thresholds to 0.4, 0.6, 0.8 permanently after noticing that the 0.2, 0.3, 0.4 thresholds would create too much inflation.

Alternative 2:

This is a more interesting one and was found by Mikael on our Discord who also helped cracking the dummy data algorithm. In short, this is how it works:

Assume that the developers created two "dummy races" (RW0 and RW-1), where they assigned points equal to an asset’s starting value (eg: NOR scored 29 pts in both dummy races). So all assets start with a PPM of 1.0 for those 2 races. If we then calculate the AvgPPM including R1, assets that get above 1.0 PPM outperformed their current price and increase. Assets below it underperformed and lose value.

The great, good, poor, terrible PPM thresholds are in the table below:

| AvgPPM       | Performance |
|--------------|-------------|
| 1.1 < x      | Great       |
| 1 < x < 1.1  | Good        |
| 0.9 < x < 1  | Poor        |
| x < 0.9      | Terrible    |

This algorithm feels pretty intuitive, works for all data points for race 1 and 2 and has the middle point of increase vs decrease at a nice PPM of 1. But because it’s further away in implementation from the dummy data algorithm, we think it’s less likely this is the currently used one. We’ll quickly see in future races which set of AvgPPM thresholds is correct.

7. Closing remarks
   We’ll end with a quote from our previous article:

“… we also think it’s possible they’ll make some adjustments to the specifics after a few weeks or maybe even for this Sunday. The 0.2, 0.3, 0.4 thresholds are nice round values that are likely easily adjustable to something more fitting like 0.4, 0.6 and 0.8.”

Which is exactly what they did, so we’re pretty happy they came to the same conclusions (or even read our post and listened to us 👀). Hopefully they’ll do the same to solve the remaining issues 🤞

*PPM values used for the Equal PPM sim in the Team Calculator turned out to the too high. Luckily, this doesn’t really matter for the effectiveness of the simulation, but it has been fixed nonetheless.

