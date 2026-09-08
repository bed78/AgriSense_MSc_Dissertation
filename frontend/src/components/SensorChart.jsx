import React, { useState, useEffect, useRef } from 'react';
import Plotly from 'plotly.js-basic-dist-min';

const SENSOR_COLORS = [
    '#FF5722', '#03A9F4', '#4CAF50', '#E91E63',
    '#FFC107', '#9C27B0', '#00BCD4', '#FF9800',
    '#3F51B5', '#8BC34A',
];

export default function SensorChart({ sensorIds, dateFrom, dateTo }) {
    const [data,    setData]    = useState(null);
    const [loading, setLoading] = useState(false);
    const plotRef = useRef(null);

    useEffect(() => {
        if (!sensorIds || sensorIds.length === 0) return;
        const fetchData = async () => {
            setLoading(true);
            try {
                const res = await fetch(
                    `http://localhost:8000/sensors/batch/data?sensor_ids=${sensorIds.join(',')}&date_from=${dateFrom}&date_to=${dateTo}`
                );
                setData(await res.json());
            } catch (e) {
                console.error('SensorChart fetch failed:', e);
            } finally {
                setLoading(false);
            }
        };
        fetchData();
    }, [sensorIds, dateFrom, dateTo]);

    useEffect(() => {
        if (!plotRef.current || !data) return;

        const traces = [];
        let colorIndex = 0;

        Object.keys(data).forEach(sid => {
            const sd = data[sid];
            if (!sd.t || sd.t.length === 0) return;
            const color = SENSOR_COLORS[colorIndex % SENSOR_COLORS.length];

            // Temperature — solid line, circle markers, left axis
            traces.push({
                x: sd.t, y: sd.temp,
                type: 'scatter', mode: 'lines+markers',
                name: `${sid} Temp`,
                line:   { color, width: 2, dash: 'solid' },
                marker: { size: 6, symbol: 'circle', color },
                yaxis: 'y',
                legendgroup: sid,
            });

            // Watermark — dashed line, diamond markers, right axis
            traces.push({
                x: sd.t, y: sd.watermark,
                type: 'scatter', mode: 'lines+markers',
                name: `${sid} Watermark`,
                line:   { color, width: 2, dash: 'dot' },
                marker: { size: 7, symbol: 'diamond', color },
                yaxis: 'y2',
                legendgroup: sid,
            });

            colorIndex++;
        });

        const layout = {
            paper_bgcolor: 'transparent',
            plot_bgcolor:  'transparent',
            font:          { color: '#d1d4dc' },
            autosize:      true,
            margin:        { l: 60, r: 60, b: 50, t: 30 },
            showlegend:    true,
            legend:        { orientation: 'h', y: -0.2 },
            hovermode:     'x unified',
            yaxis: {
                title:      'Temperature °C',
                titlefont:  { color: '#d1d4dc', size: 13 },
                tickfont:   { color: '#d1d4dc' },
                gridcolor:  '#2a2e39',
                zeroline:   false,
            },
            yaxis2: {
                title:      'Watermark cb',
                titlefont:  { color: '#d1d4dc', size: 13 },
                tickfont:   { color: '#d1d4dc' },
                overlaying: 'y',
                side:       'right',
                showgrid:   false,
                zeroline:   false,
            },
            xaxis: {
                gridcolor: '#2a2e39',
                tickfont:  { color: '#8a91a4' },
                tickangle: -45,
            },
        };

        Plotly.react(plotRef.current, traces, layout, {
            responsive:    true,
            displaylogo:   false,
            modeBarButtonsToRemove: ['toImage', 'sendDataToCloud'],
        });
    }, [data]);

    // Resize when container resizes
    useEffect(() => {
        if (!plotRef.current) return;
        const ro = new ResizeObserver(() => Plotly.Plots.resize(plotRef.current));
        ro.observe(plotRef.current);
        return () => ro.disconnect();
    }, []);

    if (!sensorIds || sensorIds.length === 0)
        return <div style={{ padding: '20px', color: '#8a91a4' }}>Please select at least one sensor from the map.</div>;
    if (loading)
        return <div style={{ padding: '20px', color: '#8a91a4' }}>Loading multi-node chart data...</div>;
    if (!data || Object.keys(data).length === 0)
        return <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100%', color: '#888' }}>
            No chart data available for the selected sensors and date range.
        </div>;

    return <div ref={plotRef} style={{ width: '100%', height: '100%' }} />;
}
