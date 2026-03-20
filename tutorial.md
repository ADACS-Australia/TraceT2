# Tutorial

In this tutorial, we will guide you through writing your first trigger.

Our aim:

* Listen to notices about gamma ray bursts (GRBs) from thet SWIFT telescope
* Filter these events according to a number of conditions
* Trigger observations with the Murchison Widefield Array (MWA) telescope.

## Getting acquinted with the SWIFT notices

First, go to https://tracet2.duckdns.org/notices/

We are interested in notices with the topics `gcn.classic.voevent.SWIFT_BAT_GRB_POS_ACK` or `gcn.classic.voevent.SWIFT_XRT_POSITION`. Use the filter to select one of these. For example:

![notice filter](tutorial/notice-filter.png)

Let's view one of these notices and inspect the associated payload:

![](tutorial/notice-detail.png)

This payload is in XML format and includes some metadata such as the notice author, the notice date, and then a lot of information about the instrument and the event itself.

At this stage, we are interested in two things:

* identifying the event ID
* identifying the event time

For SWIFT, notices are grouped together using an event ID known as the `TrigID`. Every SWIFT notice has this field and this allows us to know when disparate notices are referring to the same astronomical event.

Using [XPath](index.html#xpath-and-jsonpath), we can extract the `TrigID` with the following selector:

```
/voe:VOEvent/What/Param[@name="TrigID"]/@value
```

Further down in the XML, we can find the event time. Note that it's important to distinguish between the different times in the payload. In this case, we are not interested in the time the notice was created, we want the time of the event itself.

![](tutorial/notice-detail2.png)


Using XPath, we can extract the `ISOTime` value as:

```
/voe:VOEvent/WhereWhen/ObsDataLocation/ObservationLocation/AstroCoords/Time/TimeInstant/ISOTime/text()
```

Check that the path to the event ID and event time is consistent across each of the notice types.

## Creating an empty trigger

Now that we understand where the event ID and time are located in the SWIFT notices, let's create an empty trigger.

Go to `/triggers/create` and:

* Give it a descriptive name
* Select both `gcn.classic.voevent.SWIFT_BAT_GRB_POS_ACK` and `gcn.classic.voevent.SWIFT_XRT_POSITION` topics (by holding down the Control or Command buttons)
* Enter the event ID and time paths we identified earlier
* And set a reasonable expiry: how long after the event occurs is it still useful for us to trigger a downstream observation?

For the MWA, let's say that we care up to 24 hours, or 1440 minutes, after the event is first detected.

Altogether, our trigger configuration looks like this:

![](tutorial/trigger-initial.png)

For now, ignore the conditions and ignore the telescope configuration. Click save.

## Exploring the event history

If the trigger saved successfully, you will be presented with the trigger summary page which consists of two parts:

* a summary of the trigger configuration
* and a list of events extracted from your choice of subscribed topics

The events listing show us the history of events based on the archive of notices.

Crucially, it also shows, the `Current conditions` result. This result evaluates the set of configured conditions and shows the (hypothetical) results. We'll use these later when creating our conditions.

For now, if you hover over the single green traffic light for each notice you'll see a tooltip appear that describes the condition. As you'll see, this condition is the expiry time we set of 24 hours.

![](tutorial/trigger-events.png)

Click through the associated notices and get a sense for the kinds of information each notice contains.

## Adding some conditions

We want to add some conditions to our trigger. Specifically:

* We want to avoid observing within 5° of the equator, or having a Declination greater than +10°.
* The error radius must be less than 0.05° (and strictly greater than 0, which seems to indicate an error)
* We want an integration time of less than 2.048 seconds (for anything greater we will leave it to be manually overridden).
* We require SWIFT's starlock to be working correctly (otherwise the instrument might be reporting garbage coordinate values).

Let's start by adding the equatorial condition. Click "edit trigger" and then "Add numeric range condtion":

![](tutorial/trigger-range-condition.png)

In the lower and upper fields, we can enter -5 and +5. The selector is the XPath to the declination value, which in this case is:

```
/voe:VEvent/WhereWhen/ObsDataLocation/ObservationLocation/AstroCoords/Position2D/Value2/C2/text()
```

Finally, if this condition is true we want to return a FAIL result. When complete, the configuration looks like this:

![](tutorial/trigger-equator-condition.png)

Click save and now inspect the event listing: you'll notice a second traffic light appears under the "Current conditions" that indicates this new equatorial declination condition.

For a trigger to be successful, every condition must return a PASS. A single FAIL result we caust the trigger overall to fail.

> **Now it's your turn:** Have a go adding numeric range conditions for the following:
> * The second declination requirement (Dec< 10°)
> * The error radius (0 < err < 0.05)
> * The integration time (`Integ_Time` > 2.048).

We also require the starlock to be working. For this, we will use a boolean condition: we need `StarTrack_Lost_Lock` to be false.

When complete we have the following conditions:

![](tutorial/trigger-all-conditions.png)

You may note that the integration time is set to MAYBE if false. MAYBE is a special value that is treated differently depending on what caused the trigger evaluation. In this case, if the integration time is less than 2.048, the MAYBE result allows for manual override. See [here](index.html#conditions) for more information.

Finally, save the trigger.

## Verifying the conditions

We can inspect each archive of events and verify whether our current set of conditions correctly captures the events we want  (and ignores the ones we don't).

Consider, for example, the following event:

![](tutorial/trigger-events-full.png)

You can see that at the time of the first notice all conditions passed with the exception of one. Hovering over that particular traffic light we can see that this is the error radius condition: the first notice has an error radius that is too large.

By the second notice, however, the updated coordinates have an error radius that is sufficiently small.

Note the washed out traffic lights. TRACE-T uses [condition inheritance](index.html#condition-inheritance). In this case both the integration time and star lock values are only given in the first notice and TRACE-T assumes that, in the absence of any other overriding values, these conditions remain satisfied.

## Configuration the telescope

If you've verified that everything looks OK, it's time to finally add the configuration for the telescope. Click "edit trigger" and select your desired downstream telescope.

In this case we are going to use the MWA Correaltor with the following configuration:

![](tutorial/trigger-telescope.png)

## Manually triggering

Since our trigger is still set to inactive, it is safe to retrigger on an old event.

Find an event that your trigger passes on and click "Retrigger". If the telescope is correctly configured, you should see a green observation button:

![](tutorial/trigger-retrigger.png)

An orange or red observation button indicates a failure of some sort. In either case, you can click the button to inspect the observation details which includes the log. If an error occurred, the log can help you understand why.

## Setting your trigger as active

If everying is OK with your trigger up to this point and you've confirmed your trigger works against numerous archived events, you can ask an administrator to mark your trigger as active.
