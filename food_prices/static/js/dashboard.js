/**
 * Skripta za dinamično krmiljenje nadzorne plošče statističnih cen (Anti-Wolt)
 * 
 * Skrbi za:
 * - Asinhrono pridobivanje podatkov (fetch API) ob nalaganju strani (DOMContentLoaded).
 * - Upodabljanje in posodabljanje interaktivnih grafov s knjižnico Chart.js.
 * - Dinamično filtriranje tabele živil (iskanje, kategorija, leto).
 * - Primerjavo stroškov domače kuhinje napram gostinski ponudbi (Anti-Wolt metrika).
 * - Osvežitev podatkov neposredno iz SiStat / OPSI prek POST klica.
 */

// Globalne reference na primerke grafov (za uničenje in ponovno risanje ob spremembi podatkov)
let chartComparisonInstance = null;
let chartHistoryInstance = null;
let chartInflationInstance = null;

// Shranjeni metapodatki
let appMetadata = null;

// Ob zagonu dokumenta (DOMContentLoaded)
document.addEventListener('DOMContentLoaded', async () => {
    console.log("Inicializacija nadzorne plošče Anti-Wolt ...");
    
    // 1. Naloži začetne metapodatke (leta, kategorije)
    await initMetadataAndFilters();

    // 2. Dinamično napolni grafe in tabelo ob nalaganju
    const defaultYear = appMetadata && appMetadata.years ? appMetadata.years[appMetadata.years.length - 1] : 2025;
    
    await Promise.all([
        loadAntiWoltComparison(defaultYear),
        loadInflationLeaders(2018, defaultYear),
        loadProductHistory('Testenine'),
        loadFilteredPrices()
    ]);

    // 3. Poveži poslušalce dogodkov za interaktivno filtriranje
    setupEventListeners();
});

/**
 * Pridobi metapodatke o tabeli ter inicializira spustne sezname (dropdown-e).
 */
async function initMetadataAndFilters() {
    try {
        const response = await fetch('/api/metadata');
        const json = await response.json();
        
        if (json.status !== 'success') {
            throw new Error(json.message || "Neuspešen klic metapodatkov");
        }

        appMetadata = json.data;

        // Posodobi KPI kartice
        document.getElementById('kpi-products-count').textContent = `${appMetadata.distinct_products} izdelkov`;
        if (appMetadata.years && appMetadata.years.length > 0) {
            const minYear = appMetadata.years[0];
            const maxYear = appMetadata.years[appMetadata.years.length - 1];
            document.getElementById('kpi-years-range').textContent = `${minYear} – ${maxYear}`;
        }

        // Napolni izbirnik let za primerjalnik in tabelo
        const yearSelectComp = document.getElementById('comparison-year-select');
        const yearSelectFilter = document.getElementById('filter-year');
        yearSelectComp.innerHTML = '';
        yearSelectFilter.innerHTML = '';

        appMetadata.years.slice().reverse().forEach(y => {
            const opt1 = new Option(y, y);
            const opt2 = new Option(y, y);
            yearSelectComp.add(opt1);
            yearSelectFilter.add(opt2);
        });

        // Napolni izbirnik kategorij za tabelo
        const catSelect = document.getElementById('filter-category');
        catSelect.innerHTML = '<option value="Vse">Vse prehranske kategorije</option>';
        appMetadata.food_categories.forEach(cat => {
            const opt = new Option(cat, cat);
            catSelect.add(opt);
        });

        // Napolni začetni nabor izdelkov za zgodovino
        initProductHistoryDropdown();

    } catch (err) {
        console.error("Napaka pri nalaganju metapodatkov:", err);
    }
}

/**
 * Napolni spustni meni z najbolj priljubljenimi živili za časovni trend.
 */
function initProductHistoryDropdown() {
    const historySelect = document.getElementById('product-history-select');
    historySelect.innerHTML = '';

    const popularProducts = [
        'Testenine',
        'Riž, beli',
        'Kruh (bel, polbel, črn)',
        'Moka, pšenična, razen polnozrnata',
        'Mleko, trajno, polnomastno',
        'Sir',
        'Jajca',
        'Meso, piščančje brez kosti',
        'Meso, goveje brez kosti',
        'Maslo',
        'Kava, klasična',
        'Jabolka',
        'Paradižnik, sveži',
        'Pica, velika',
        'Meni, kosilo'
    ];

    popularProducts.forEach(prod => {
        const opt = new Option(prod, prod);
        historySelect.add(opt);
    });
}

/**
 * Naloži in izriše Anti-Wolt primerjalni graf med domačim kuhanjem in gostinstvom.
 */
async function loadAntiWoltComparison(year) {
    const container = document.getElementById('comparison-cards-container');
    try {
        const response = await fetch(`/api/comparison?year=${year}`);
        const res = await response.json();
        
        if (res.status !== 'success') return;
        const data = res.data;

        // Posodobi povprečni prihranek v KPI kartici
        document.getElementById('kpi-avg-savings').textContent = `~${data.average_savings_pct} %`;

        // Priprava podatkov za Chart.js
        const labels = data.comparisons.map(c => c.meal);
        const homeCosts = data.comparisons.map(c => c.home_cost);
        const restCosts = data.comparisons.map(c => c.restaurant_cost);

        renderComparisonChart(labels, homeCosts, restCosts, year);

        // Dinamično ustvari informativne kartice s prihranki
        container.innerHTML = '';
        data.comparisons.forEach(c => {
            const card = document.createElement('div');
            card.className = 'bg-slate-50 border border-slate-200 rounded-lg p-3 text-xs flex justify-between items-center hover:bg-emerald-50 transition';
            card.innerHTML = `
                <div>
                    <p class="font-bold text-slate-800">${c.meal}</p>
                    <p class="text-slate-500 text-[11px]">${c.home_title}</p>
                    <div class="mt-1 flex items-center space-x-2">
                        <span class="inline-block px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-800 font-semibold font-mono">Doma: ${c.home_cost.toFixed(2)} €</span>
                        <span class="text-slate-400">vs</span>
                        <span class="inline-block px-1.5 py-0.5 rounded bg-rose-100 text-rose-800 font-semibold font-mono">Gostinstvo: ${c.restaurant_cost.toFixed(2)} €</span>
                    </div>
                </div>
                <div class="text-right pl-3">
                    <p class="text-base font-extrabold text-emerald-600 font-mono">-${c.savings_pct}%</p>
                    <p class="text-[11px] text-slate-500 font-medium font-mono">Prihranek ${c.savings_eur.toFixed(2)} €</p>
                </div>
            `;
            container.appendChild(card);
        });

    } catch (err) {
        console.error("Napaka pri nalaganju Anti-Wolt primerjave:", err);
    }
}

/**
 * Izris stolpčnega grafa Anti-Wolt primerjave z uporabo Chart.js.
 */
function renderComparisonChart(labels, homeCosts, restCosts, year) {
    const ctx = document.getElementById('chartComparison').getContext('2d');
    
    if (chartComparisonInstance) {
        chartComparisonInstance.destroy();
    }

    chartComparisonInstance = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [
                {
                    label: 'Domača priprava (ocene surovin)',
                    data: homeCosts,
                    backgroundColor: 'rgba(16, 185, 129, 0.85)', // Emerald
                    borderColor: 'rgb(16, 185, 129)',
                    borderWidth: 1,
                    borderRadius: 6
                },
                {
                    label: 'Gostinska porcija / dostava',
                    data: restCosts,
                    backgroundColor: 'rgba(239, 68, 68, 0.85)', // Rdeča
                    borderColor: 'rgb(239, 68, 68)',
                    borderWidth: 1,
                    borderRadius: 6
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    position: 'top',
                    labels: { boxWidth: 14, font: { size: 11, family: 'sans-serif' } }
                },
                tooltip: {
                    callbacks: {
                        label: function(context) {
                            return ` ${context.dataset.label}: ${context.parsed.y.toFixed(2)} €`;
                        }
                    }
                }
            },
            scales: {
                y: {
                    beginAtZero: true,
                    ticks: {
                        callback: val => `${val} €`,
                        font: { size: 10 }
                    },
                    grid: { color: 'rgba(226, 232, 240, 0.6)' }
                },
                x: {
                    ticks: { font: { size: 11, weight: 'bold' } },
                    grid: { display: false }
                }
            }
        }
    });
}

/**
 * Naloži časovni trend cen za izbrani izdelek.
 */
async function loadProductHistory(productName) {
    try {
        const response = await fetch(`/api/history?product=${encodeURIComponent(productName)}`);
        const res = await response.json();
        
        if (res.status !== 'success' || !res.data.found) {
            console.warn("Izdelek ni bil najden:", productName);
            return;
        }

        const data = res.data;

        // Posodobi informativno vrstico
        document.getElementById('history-prod-name').textContent = data.product_name;
        document.getElementById('history-prod-unit').textContent = `(${data.unit})`;
        
        const sign = data.diff_eur >= 0 ? '+' : '';
        const changeSpan = document.getElementById('history-prod-change');
        changeSpan.textContent = `${sign}${data.diff_eur.toFixed(2)} € (${sign}${data.diff_pct.toFixed(1)} %)`;
        
        if (data.diff_eur > 0) {
            changeSpan.className = 'font-bold text-rose-600 ml-1 font-mono';
        } else {
            changeSpan.className = 'font-bold text-emerald-600 ml-1 font-mono';
        }

        renderHistoryChart(data.years, data.prices, data.product_name, data.unit);

    } catch (err) {
        console.error("Napaka pri nalaganju zgodovine izdelka:", err);
    }
}

/**
 * Izris linijskega grafa časovnega trenda s Chart.js.
 */
function renderHistoryChart(years, prices, productName, unit) {
    const ctx = document.getElementById('chartHistory').getContext('2d');

    if (chartHistoryInstance) {
        chartHistoryInstance.destroy();
    }

    chartHistoryInstance = new Chart(ctx, {
        type: 'line',
        data: {
            labels: years,
            datasets: [{
                label: `Cena v EUR (${unit})`,
                data: prices,
                borderColor: 'rgb(37, 99, 235)', // Modra
                backgroundColor: 'rgba(37, 99, 235, 0.1)',
                borderWidth: 2.5,
                fill: true,
                tension: 0.3,
                pointBackgroundColor: 'rgb(37, 99, 235)',
                pointRadius: 4,
                pointHoverRadius: 6
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        label: ctx => ` Cena: ${ctx.parsed.y.toFixed(2)} € / ${unit}`
                    }
                }
            },
            scales: {
                y: {
                    beginAtZero: false,
                    ticks: {
                        callback: val => `${val.toFixed(2)} €`,
                        font: { size: 10 }
                    },
                    grid: { color: 'rgba(226, 232, 240, 0.6)' }
                },
                x: {
                    grid: { display: false },
                    ticks: { font: { size: 10 } }
                }
            }
        }
    });
}

/**
 * Naloži rekorderje podražitev (inflacijo) in izriše vodoravni stolpčni graf.
 */
async function loadInflationLeaders(startYear = 2018, endYear = 2025) {
    try {
        const response = await fetch(`/api/inflation?start_year=${startYear}&end_year=${endYear}&limit=7`);
        const res = await response.json();

        if (res.status !== 'success') return;
        const items = res.data;

        const labels = items.map(i => i.product_name);
        const pctValues = items.map(i => i.diff_pct);

        const ctx = document.getElementById('chartInflation').getContext('2d');
        if (chartInflationInstance) {
            chartInflationInstance.destroy();
        }

        chartInflationInstance = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [{
                    label: `Podražitev v % (${startYear} → ${endYear})`,
                    data: pctValues,
                    backgroundColor: 'rgba(225, 29, 72, 0.8)', // Rose
                    borderColor: 'rgb(225, 29, 72)',
                    borderWidth: 1,
                    borderRadius: 4
                }]
            },
            options: {
                indexAxis: 'y', // Vodoravni stolpci
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        callbacks: {
                            label: function(ctx) {
                                const item = items[ctx.dataIndex];
                                return ` Rast: +${ctx.parsed.x.toFixed(1)}% (${item.start_price.toFixed(2)}€ → ${item.end_price.toFixed(2)}€)`;
                            }
                        }
                    }
                },
                scales: {
                    x: {
                        beginAtZero: true,
                        ticks: {
                            callback: val => `+${val}%`,
                            font: { size: 10 }
                        },
                        grid: { color: 'rgba(226, 232, 240, 0.6)' }
                    },
                    y: {
                        ticks: { font: { size: 10 } },
                        grid: { display: false }
                    }
                }
            }
        });

    } catch (err) {
        console.error("Napaka pri nalaganju rekorderjev inflacije:", err);
    }
}

/**
 * Dinamično naloži in prikaže tabelo s cenami glede na izbrane filtre.
 */
async function loadFilteredPrices() {
    const year = document.getElementById('filter-year').value;
    const category = document.getElementById('filter-category').value;
    const search = document.getElementById('filter-search').value.trim();

    const tbody = document.getElementById('prices-table-body');
    tbody.innerHTML = `
        <tr>
            <td colspan="6" class="text-center py-6 text-slate-400">
                <i class="fa-solid fa-spinner fa-spin text-lg"></i>
                <p class="mt-1">Nalagam podatke o cenah ...</p>
            </td>
        </tr>
    `;

    try {
        let url = `/api/prices?year=${year}`;
        if (category && category !== 'Vse') {
            url += `&category=${encodeURIComponent(category)}`;
        }
        if (search) {
            url += `&search=${encodeURIComponent(search)}`;
        }

        const response = await fetch(url);
        const res = await response.json();

        if (res.status !== 'success') {
            tbody.innerHTML = `<tr><td colspan="6" class="text-center text-rose-500 py-4">Napaka pri pridobivanju podatkov.</td></tr>`;
            return;
        }

        const rows = res.data;
        document.getElementById('table-results-count').textContent = `Prikazujem ${rows.length} rezultatov`;

        if (rows.length === 0) {
            tbody.innerHTML = `<tr><td colspan="6" class="text-center py-6 text-slate-400">Ni zadetkov za izbrane filtre.</td></tr>`;
            return;
        }

        tbody.innerHTML = '';
        rows.forEach(item => {
            const tr = document.createElement('tr');
            tr.className = 'border-b border-slate-100 hover:bg-slate-50 transition';
            tr.innerHTML = `
                <td class="px-4 py-2.5 font-medium text-slate-800">${item.product_name}</td>
                <td class="px-4 py-2.5 text-slate-500 font-mono">${item.unit}</td>
                <td class="px-4 py-2.5 text-slate-500 text-[11px]">${item.category}</td>
                <td class="px-4 py-2.5 text-slate-600 font-mono">${item.year}</td>
                <td class="px-4 py-2.5 text-right font-bold text-slate-800 font-mono">${item.price_eur.toFixed(2)} €</td>
                <td class="px-4 py-2.5 text-center">
                    <button class="btn-show-trend inline-flex items-center px-2 py-1 rounded bg-blue-50 hover:bg-blue-100 text-blue-700 text-[11px] font-medium transition"
                            data-product="${item.product_name}">
                        <i class="fa-solid fa-chart-line mr-1"></i> Trend
                    </button>
                </td>
            `;
            tbody.appendChild(tr);
        });

        // Dodaj dogodke za gumbe 'Trend' v tabeli
        document.querySelectorAll('.btn-show-trend').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const prod = e.currentTarget.getAttribute('data-product');
                // Posodobi izbirnik in naloži graf
                document.getElementById('product-history-select').value = prod;
                loadProductHistory(prod);
                // Pomakni se do grafa
                document.getElementById('chartHistory').scrollIntoView({ behavior: 'smooth', block: 'center' });
            });
        });

    } catch (err) {
        console.error("Napaka pri filtriranju tabele:", err);
        tbody.innerHTML = `<tr><td colspan="6" class="text-center text-rose-500 py-4">Prišlo je do napake pri prenosu podatkov.</td></tr>`;
    }
}

/**
 * Povezava poslušalcev dogodkov (event listeners) za interaktivnost.
 */
function setupEventListeners() {
    // 1. Sprememba leta pri Anti-Wolt primerjalniku
    document.getElementById('comparison-year-select').addEventListener('change', (e) => {
        loadAntiWoltComparison(e.target.value);
    });

    // 2. Sprememba izbranega živila za zgodovino
    document.getElementById('product-history-select').addEventListener('change', (e) => {
        loadProductHistory(e.target.value);
    });

    // 3. Filtri tabele
    document.getElementById('filter-year').addEventListener('change', loadFilteredPrices);
    document.getElementById('filter-category').addEventListener('change', loadFilteredPrices);

    // Iskalno polje z zakasnitvijo (debounce)
    let searchTimeout = null;
    document.getElementById('filter-search').addEventListener('input', () => {
        clearTimeout(searchTimeout);
        searchTimeout = setTimeout(loadFilteredPrices, 300);
    });

    // 4. Gumb za prisilno osvežitev podatkov iz OPSI / SiStat
    const btnRefresh = document.getElementById('btn-refresh');
    const refreshIcon = document.getElementById('btn-refresh-icon');

    btnRefresh.addEventListener('click', async () => {
        btnRefresh.disabled = true;
        refreshIcon.classList.add('spinning');

        try {
            const resp = await fetch('/api/refresh', { method: 'POST' });
            const json = await resp.json();

            if (json.status === 'success') {
                alert("Uspešno osveženo! Preneseni so sveži podatki iz SiStat (0411005S.px).");
                // Ponovno naloži vse poglede
                await initMetadataAndFilters();
                const defaultYear = appMetadata.years[appMetadata.years.length - 1];
                await Promise.all([
                    loadAntiWoltComparison(defaultYear),
                    loadInflationLeaders(2018, defaultYear),
                    loadProductHistory(document.getElementById('product-history-select').value || 'Testenine'),
                    loadFilteredPrices()
                ]);
            } else {
                alert(`Napaka pri osveževanju: ${json.message}`);
            }
        } catch (err) {
            console.error("Napaka pri klicu osvežitve:", err);
            alert("Napaka pri povezavi s strežnikom.");
        } finally {
            btnRefresh.disabled = false;
            refreshIcon.classList.remove('spinning');
        }
    });
}

