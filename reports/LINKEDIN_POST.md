# LinkedIn post

**Attach these 4 images (in this order):**
1. `outputs/figures/q1_trends_dashboard.png`
2. `outputs/figures/q3_charger_gen_heat.png`
3. `outputs/figures/q4_battery_quality.png`
4. `outputs/figures/q6_cohort_churn.png`

---

Volume up 2.8×. Revenue up 3×. So why were riders leaving? 🔋

For the Data Analytics Hackathon by Gradient Learnings, I analysed 3.8 million battery-swap events from VoltRelay, a (fictional) EV battery-swapping network for delivery riders in 6 Indian cities.

Leadership had 6 suspects: two expansion waves, a price rise, peak pricing, a new battery supplier and a new fleet contract. The data pointed at three of them.

1️⃣ Old chargers + summer heat
Gen1 chargers take 88 min to charge a pack, but 190 min when the cabinet hits 50°C. Cabinets run out of charged batteries. Gen1 stations failed 12.7% of swaps vs 5.1% at new Gen3 stations. Jaipur, Delhi and Hyderabad have the most Gen1 stations, and they have the highest failure rates.

2️⃣ One bad battery batch
3 lots from the new supplier (1,461 packs) dropped to 61% health within a year, vs 81% for everything else. They gave 19% less range, caused 2.6× more range complaints, and ₹4.8 crore of packs were retired on a single day.

3️⃣ A discount that bought nothing
The largest fleet partner's discount went from 12% to 28%. Contribution per swap fell 25%, and their volume grew no faster than the rest of the network.

What did not cause it: the station expansion (new stations perform best), firmware, connectivity, and the competitor.

The churn finding I liked most: new riders whose first-month failure rate was under 5% churned at 10.7%. Above 20%, it was 39.5%. The first 30 days decide whether a rider stays.

Method lesson: a simple "had a failed swap: yes/no" flag made failures look harmless, because heavy users see more failures and also stay. Measuring the failure rate per attempt showed the real effect.

My recommendation: spend the budget on replacing Gen1 chargers in the hottest cities before next summer, recover costs from the supplier, and tie fleet discounts to off-peak usage.

Built with Python, DuckDB (SQL), pandas, statsmodels and matplotlib in Google Colab. AI tools helped with code and documentation, and I checked every number against the data.

Thanks to Gradient Learnings for a dataset that felt like a real business problem 🙌

#DataAnalytics #Hackathon #GradientLearnings #EV #Python #SQL #DataStorytelling #RootCauseAnalysis
