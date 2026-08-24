# Report QA

`delivery-receipt.json` is the browser verification for the honest pending
scheme-one artifact; `delivery-receipt-two.json` verifies the pending scheme-two
artifact. `completed-layout-receipt.json` verifies the scheme-one
ready-state layout (coverage chart, two heatmaps, seven status cards, and the
320-cell table) with synthetic values; those values and their temporary HTML are
deliberately not shipped and are not performance evidence. Both checks cover
1440px desktop and 390px mobile views; the verifier also rejects external
requests and exercises the source dialog.
