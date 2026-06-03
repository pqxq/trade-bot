let equityChart = null;

async function fetchEquityData() {
  const response = await fetch("/api/equity");
  return response.json();
}

function renderEquityChart(points) {
  const labels = points.map((point) => new Date(point.timestamp).toLocaleString());
  const values = points.map((point) => point.balance);

  const ctx = document.getElementById("equityChart");
  if (!ctx) {
    return;
  }

  if (equityChart) {
    equityChart.data.labels = labels;
    equityChart.data.datasets[0].data = values;
    equityChart.update();
    return;
  }

  equityChart = new Chart(ctx, {
    type: "line",
    data: {
      labels: labels,
      datasets: [
        {
          label: "Equity",
          data: values,
          borderColor: "#0d6efd",
          backgroundColor: "rgba(13, 110, 253, 0.1)",
          tension: 0.2,
        },
      ],
    },
    options: {
      responsive: true,
      scales: {
        x: {
          display: true,
        },
        y: {
          display: true,
        },
      },
    },
  });
}

async function refreshEquityChart() {
  try {
    const data = await fetchEquityData();
    renderEquityChart(data);
  } catch (error) {
    console.error("Failed to refresh equity chart", error);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  refreshEquityChart();
  setInterval(refreshEquityChart, 30000);
});
