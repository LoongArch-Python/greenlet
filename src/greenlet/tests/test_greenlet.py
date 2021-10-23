from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import gc
import sys
import time
import threading

from abc import ABCMeta, abstractmethod

from greenlet import greenlet
from . import TestCase
from .leakcheck import fails_leakcheck

# We manually manage locks in many tests
# pylint:disable=consider-using-with
# pylint:disable=too-many-public-methods

class SomeError(Exception):
    pass


def fmain(seen):
    try:
        greenlet.getcurrent().parent.switch()
    except:
        seen.append(sys.exc_info()[0])
        raise
    raise SomeError


def send_exception(g, exc):
    # note: send_exception(g, exc)  can be now done with  g.throw(exc).
    # the purpose of this test is to explicitely check the propagation rules.
    def crasher(exc):
        raise exc
    g1 = greenlet(crasher, parent=g)
    g1.switch(exc)


class TestGreenlet(TestCase):

    def _do_simple_test(self):
        lst = []

        def f():
            lst.append(1)
            greenlet.getcurrent().parent.switch()
            lst.append(3)
        g = greenlet(f)
        lst.append(0)
        g.switch()
        lst.append(2)
        g.switch()
        lst.append(4)
        self.assertEqual(lst, list(range(5)))

    def test_simple(self):
        self._do_simple_test()

    def test_switch_no_run_raises_AttributeError(self):
        g = greenlet()
        with self.assertRaises(AttributeError) as exc:
            g.switch()

        self.assertIn("run", str(exc.exception))

    def test_throw_no_run_raises_AttributeError(self):
        g = greenlet()
        with self.assertRaises(AttributeError) as exc:
            g.throw(SomeError)

        self.assertIn("run", str(exc.exception))

    def test_parent_equals_None(self):
        g = greenlet(parent=None)
        self.assertIsNotNone(g)
        self.assertIs(g.parent, greenlet.getcurrent())

    def test_run_equals_None(self):
        g = greenlet(run=None)
        self.assertIsNotNone(g)
        self.assertIsNone(g.run)

    def test_two_children(self):
        lst = []

        def f():
            lst.append(1)
            greenlet.getcurrent().parent.switch()
            lst.extend([1, 1])
        g = greenlet(f)
        h = greenlet(f)
        g.switch()
        self.assertEqual(len(lst), 1)
        h.switch()
        self.assertEqual(len(lst), 2)
        h.switch()
        self.assertEqual(len(lst), 4)
        self.assertEqual(h.dead, True)
        g.switch()
        self.assertEqual(len(lst), 6)
        self.assertEqual(g.dead, True)

    def test_two_recursive_children(self):
        lst = []

        def f():
            lst.append(1)
            greenlet.getcurrent().parent.switch()

        def g():
            lst.append(1)
            g = greenlet(f)
            g.switch()
            lst.append(1)
        g = greenlet(g)
        g.switch()
        self.assertEqual(len(lst), 3)
        self.assertEqual(sys.getrefcount(g), 2)

    def test_threads(self):
        success = []

        def f():
            self._do_simple_test()
            success.append(True)
        ths = [threading.Thread(target=f) for i in range(10)]
        for th in ths:
            th.start()
        for th in ths:
            th.join(10)
        self.assertEqual(len(success), len(ths))

    def test_exception(self):
        import functools
        p = functools.partial(print, file=sys.stderr)
        seen = []
        g1 = greenlet(fmain)
        g2 = greenlet(fmain)
        p()
        p("***Main greenlet", greenlet.getcurrent())
        p(f"***Created {g1=} {g2=}")
        g1.switch(seen)
        p("***SWITCH 1")
        g2.switch(seen)
        p("***SWITCH 2")
        g2.parent = g1
        p("***Reparent")
        self.assertEqual(seen, [])
        #with self.assertRaises(SomeError):
        #    p("***Switching back")
        #    g2.switch()
        # Creating this as a bound method can reveal bugs that
        # are hidden on newer versions of Python that avoid creating
        # bound methods for direct expressions; IOW, don't use the `with`
        # form!
        self.assertRaises(SomeError, g2.switch)
        self.assertEqual(seen, [SomeError])
        p("***SWITCH TO DEAD", g2)
        value = g2.switch()
        self.assertEqual(value, ())
        self.assertEqual(seen, [SomeError])

        value = g2.switch(25)
        self.assertEqual(value, 25)
        self.assertEqual(seen, [SomeError])


    def test_send_exception(self):
        seen = []
        g1 = greenlet(fmain)
        g1.switch(seen)
        self.assertRaises(KeyError, send_exception, g1, KeyError)
        self.assertEqual(seen, [KeyError])

    def test_dealloc(self):
        seen = []
        g1 = greenlet(fmain)
        g2 = greenlet(fmain)
        g1.switch(seen)
        g2.switch(seen)
        self.assertEqual(seen, [])
        del g1
        gc.collect()
        self.assertEqual(seen, [greenlet.GreenletExit])
        del g2
        gc.collect()
        self.assertEqual(seen, [greenlet.GreenletExit, greenlet.GreenletExit])

    def test_dealloc_other_thread(self):
        seen = []
        someref = []

        bg_glet_created_running_and_no_longer_ref_in_bg = threading.Event()
        fg_ref_released = threading.Event()
        bg_should_be_clear = threading.Event()
        ok_to_exit_bg_thread = threading.Event()

        def f():
            g1 = greenlet(fmain)
            g1.switch(seen)
            someref.append(g1)
            del g1
            gc.collect()
            bg_glet_created_running_and_no_longer_ref_in_bg.set()
            fg_ref_released.wait(3)
            #print("Triggering")
            greenlet()   # trigger release
            bg_should_be_clear.set()
            ok_to_exit_bg_thread.wait(3)
            greenlet() # One more time
            #print("Exiting")
        t = threading.Thread(target=f)
        t.start()
        bg_glet_created_running_and_no_longer_ref_in_bg.wait(10)

        self.assertEqual(seen, [])
        self.assertEqual(len(someref), 1)
        del someref[:]
        gc.collect()
        # g1 is not released immediately because it's from another thread
        self.assertEqual(seen, [])
        fg_ref_released.set()
        bg_should_be_clear.wait(3)
        self.assertEqual(seen, [greenlet.GreenletExit])
        ok_to_exit_bg_thread.set()
        t.join(10)
        del seen[:]
        del someref[:]

    def test_frame(self):
        def f1():
            f = sys._getframe(0) # pylint:disable=protected-access
            self.assertEqual(f.f_back, None)
            greenlet.getcurrent().parent.switch(f)
            return "meaning of life"
        g = greenlet(f1)
        frame = g.switch()
        self.assertTrue(frame is g.gr_frame)
        self.assertTrue(g)

        from_g = g.switch()
        self.assertFalse(g)
        self.assertEqual(from_g, 'meaning of life')
        self.assertEqual(g.gr_frame, None)

    def test_thread_bug(self):
        def runner(x):
            g = greenlet(lambda: time.sleep(x))
            g.switch()
        t1 = threading.Thread(target=runner, args=(0.2,))
        t2 = threading.Thread(target=runner, args=(0.3,))
        t1.start()
        t2.start()
        t1.join(10)
        t2.join(10)

    def test_switch_kwargs(self):
        def run(a, b):
            self.assertEqual(a, 4)
            self.assertEqual(b, 2)
            return 42
        x = greenlet(run).switch(a=4, b=2)
        self.assertEqual(x, 42)

    def test_switch_kwargs_to_parent(self):
        def run(x):
            greenlet.getcurrent().parent.switch(x=x)
            greenlet.getcurrent().parent.switch(2, x=3)
            return x, x ** 2
        g = greenlet(run)
        self.assertEqual({'x': 3}, g.switch(3))
        self.assertEqual(((2,), {'x': 3}), g.switch())
        self.assertEqual((3, 9), g.switch())

    def test_switch_to_another_thread(self):
        data = {}
        created_event = threading.Event()
        done_event = threading.Event()

        def run():
            data['g'] = greenlet(lambda: None)
            created_event.set()
            done_event.wait(10)
        thread = threading.Thread(target=run)
        thread.start()
        created_event.wait(10)
        with self.assertRaises(greenlet.error):
            data['g'].switch()
        done_event.set()
        thread.join(10)
        # XXX: Should handle this automatically
        data.clear()

    def test_exc_state(self):
        def f():
            try:
                raise ValueError('fun')
            except: # pylint:disable=bare-except
                exc_info = sys.exc_info()
                greenlet(h).switch()
                self.assertEqual(exc_info, sys.exc_info())

        def h():
            self.assertEqual(sys.exc_info(), (None, None, None))

        greenlet(f).switch()

    def test_instance_dict(self):
        def f():
            greenlet.getcurrent().test = 42
        def deldict(g):
            del g.__dict__
        def setdict(g, value):
            g.__dict__ = value
        g = greenlet(f)
        self.assertEqual(g.__dict__, {})
        g.switch()
        self.assertEqual(g.test, 42)
        self.assertEqual(g.__dict__, {'test': 42})
        g.__dict__ = g.__dict__
        self.assertEqual(g.__dict__, {'test': 42})
        self.assertRaises(TypeError, deldict, g)
        self.assertRaises(TypeError, setdict, g, 42)

    def test_running_greenlet_has_no_run(self):
        has_run = []
        def func():
            has_run.append(
                hasattr(greenlet.getcurrent(), 'run')
            )

        g = greenlet(func)
        g.switch()
        self.assertEqual(has_run, [False])

    def test_deepcopy(self):
        import copy
        self.assertRaises(TypeError, copy.copy, greenlet())
        self.assertRaises(TypeError, copy.deepcopy, greenlet())

    def test_parent_restored_on_kill(self):
        hub = greenlet(lambda: None)
        main = greenlet.getcurrent()
        result = []
        def worker():
            try:
                # Wait to be killed by going back to the test.
                main.switch()
            except greenlet.GreenletExit:
                # Resurrect and switch to parent
                result.append(greenlet.getcurrent().parent)
                result.append(greenlet.getcurrent())
                hub.switch()
        g = greenlet(worker, parent=hub)
        g.switch()
        # delete the only reference, thereby raising GreenletExit
        del g
        self.assertTrue(result)
        self.assertIs(result[0], main)
        self.assertIs(result[1].parent, hub)
        # Delete them, thereby breaking the cycle between the greenlet
        # and the frame, which otherwise would never be collectable
        # XXX: We should be able to automatically fix this.
        del result[:]
        hub = None
        main = None

    def test_parent_return_failure(self):
        # No run causes AttributeError on switch
        g1 = greenlet()
        # Greenlet that implicitly switches to parent
        g2 = greenlet(lambda: None, parent=g1)
        # AttributeError should propagate to us, no fatal errors
        with self.assertRaises(AttributeError):
            g2.switch()

    def test_throw_exception_not_lost(self):
        class mygreenlet(greenlet):
            def __getattribute__(self, name):
                try:
                    raise Exception()
                except: # pylint:disable=bare-except
                    pass
                return greenlet.__getattribute__(self, name)
        g = mygreenlet(lambda: None)
        self.assertRaises(SomeError, g.throw, SomeError())

    @fails_leakcheck
    def test_throw_to_dead_thread_doesnt_crash(self):
        result = []
        def worker():
            greenlet.getcurrent().parent.switch()
        def creator():
            g = greenlet(worker)
            g.switch()
            result.append(g)
        t = threading.Thread(target=creator)
        t.start()
        t.join(10)
        with self.assertRaises(greenlet.error) as exc:
            result[0].throw(SomeError)

        self.assertEqual(
            str(exc.exception),
            "cannot switch to a different thread (which happens to have exited)"
        )

        if hasattr(result[0].gr_frame, 'clear'):
            # The frame is actually executing (it thinks), we can't clear it.
            with self.assertRaises(RuntimeError):
                result[0].gr_frame.clear()
        # Unfortunately, this doesn't actually clear the references, they're in the
        # fast local array.
        result[0].gr_frame.f_locals.clear()
        del t
        del creator
        del result[:]
        # XXX: we ought to be able to automatically fix this.
        # See issue 252
        self.expect_greenlet_leak = True # direct us not to wait for it to go away


    @fails_leakcheck
    def test_recursive_startup(self):
        class convoluted(greenlet):
            def __init__(self):
                greenlet.__init__(self)
                self.count = 0
            def __getattribute__(self, name):
                if name == 'run' and self.count == 0:
                    self.count = 1
                    self.switch(43)
                return greenlet.__getattribute__(self, name)
            def run(self, value):
                while True:
                    self.parent.switch(value)
        g = convoluted()
        self.assertEqual(g.switch(42), 43)
        # Exits the running greenlet, otherwise it leaks
        # XXX: We should be able to automatically fix this
        #g.throw(greenlet.GreenletExit)
        #del g
        self.expect_greenlet_leak = True

    def test_threaded_updatecurrent(self):
        # released when main thread should execute
        lock1 = threading.Lock()
        lock1.acquire()
        # released when another thread should execute
        lock2 = threading.Lock()
        lock2.acquire()
        class finalized(object):
            def __del__(self):
                # happens while in green_updatecurrent() in main greenlet
                # should be very careful not to accidentally call it again
                # at the same time we must make sure another thread executes
                lock2.release()
                lock1.acquire()
                # now ts_current belongs to another thread
        def deallocator():
            greenlet.getcurrent().parent.switch()
        def fthread():
            lock2.acquire()
            greenlet.getcurrent()
            del g[0]
            lock1.release()
            lock2.acquire()
            greenlet.getcurrent()
            lock1.release()
        main = greenlet.getcurrent()
        g = [greenlet(deallocator)]
        g[0].bomb = finalized()
        g[0].switch()
        t = threading.Thread(target=fthread)
        t.start()
        # let another thread grab ts_current and deallocate g[0]
        lock2.release()
        lock1.acquire()
        # this is the corner stone
        # getcurrent() will notice that ts_current belongs to another thread
        # and start the update process, which would notice that g[0] should
        # be deallocated, and that will execute an object's finalizer. Now,
        # that object will let another thread run so it can grab ts_current
        # again, which would likely crash the interpreter if there's no
        # check for this case at the end of green_updatecurrent(). This test
        # passes if getcurrent() returns correct result, but it's likely
        # to randomly crash if it's not anyway.
        self.assertEqual(greenlet.getcurrent(), main)
        # wait for another thread to complete, just in case
        t.join(10)

    def test_dealloc_switch_args_not_lost(self):
        seen = []
        def worker():
            # wait for the value
            print("WORKER: Return to main", file=sys.stderr)
            # 	Called switch on 0x106309ef0 from 0x106308f30
            value = greenlet.getcurrent().parent.switch()
            # delete all references to ourself
            print("WORKER: Return from main via my child", value, file=sys.stderr)
            del worker[0]
            initiator.parent = greenlet.getcurrent().parent
            # switch to main with the value, but because
            # ts_current is the last reference to us we
            # return here immediately, where we resurrect ourself.
            try:
                print("WORKER: Switching to main with", value, file=sys.stderr)
                greenlet.getcurrent().parent.switch(value)
            # except BaseException as e:
            #     print("GOT EXCEPTION IN INIT", e, file=sys.stderr)
            #     raise
            finally:
                seen.append(greenlet.getcurrent())
        def initiator():
            return 42 # implicitly falls thru to parent
        # ThreadState 0x10290cda0 has main greenlet 0x106309ef0 ot 0x10290cda0
        worker = [greenlet(worker)]

        print("Created worker greenlet", worker, file=sys.stderr)
        # Created worker greenlet [<greenlet.greenlet object at 0x106308f30 (otid=0x0) pending>]
        # Called switch on 0x106308f30 from 0x106309ef0
        # g_switchstack: into 0x106308f30 args 0x100b04250 kwargs 0x0 err? 0x0
        worker[0].switch() # prime worker
        initiator = greenlet(initiator, worker[0])
        print("Created iniator greenlet", initiator, file=sys.stderr)
        value = initiator.switch()
        self.assertTrue(seen)
        self.assertEqual(value, 42)

    def test_tuple_subclass(self):
        if sys.version_info[0] > 2:
            # There's no apply in Python 3.x
            def _apply(func, a, k):
                func(*a, **k)
        else:
            _apply = apply # pylint:disable=undefined-variable

        class mytuple(tuple):
            def __len__(self):
                greenlet.getcurrent().switch()
                return tuple.__len__(self)
        args = mytuple()
        kwargs = dict(a=42)
        def switchapply():
            _apply(greenlet.getcurrent().parent.switch, args, kwargs)
        g = greenlet(switchapply)
        self.assertEqual(g.switch(), kwargs)

    def test_abstract_subclasses(self):
        AbstractSubclass = ABCMeta(
            'AbstractSubclass',
            (greenlet,),
            {'run': abstractmethod(lambda self: None)})

        class BadSubclass(AbstractSubclass):
            pass

        class GoodSubclass(AbstractSubclass):
            def run(self):
                pass

        GoodSubclass() # should not raise
        self.assertRaises(TypeError, BadSubclass)

    def test_implicit_parent_with_threads(self):
        if not gc.isenabled():
            return # cannot test with disabled gc
        N = gc.get_threshold()[0]
        if N < 50:
            return # cannot test with such a small N
        def attempt():
            lock1 = threading.Lock()
            lock1.acquire()
            lock2 = threading.Lock()
            lock2.acquire()
            recycled = [False]
            def another_thread():
                lock1.acquire() # wait for gc
                greenlet.getcurrent() # update ts_current
                lock2.release() # release gc
            t = threading.Thread(target=another_thread)
            t.start()
            class gc_callback(object):
                def __del__(self):
                    lock1.release()
                    lock2.acquire()
                    recycled[0] = True
            class garbage(object):
                def __init__(self):
                    self.cycle = self
                    self.callback = gc_callback()
            l = []
            x = range(N*2)
            current = greenlet.getcurrent()
            g = garbage()
            for _ in x:
                g = None # lose reference to garbage
                if recycled[0]:
                    # gc callback called prematurely
                    t.join(10)
                    return False
                last = greenlet()
                if recycled[0]:
                    break # yes! gc called in green_new
                l.append(last) # increase allocation counter
            else:
                # gc callback not called when expected
                gc.collect()
                if recycled[0]:
                    t.join(10)
                return False
            self.assertEqual(last.parent, current)
            for g in l:
                self.assertEqual(g.parent, current)
            return True
        for _ in range(5):
            if attempt():
                break

    def test_issue_245_reference_counting_subclass_no_threads(self):
        # https://github.com/python-greenlet/greenlet/issues/245
        # Before the fix, this crashed pretty reliably on
        # Python 3.10, at least on macOS; but much less reliably on other
        # interpreters (memory layout must have changed).
        # The threaded test crashed more reliably on more interpreters.
        from greenlet import getcurrent
        from greenlet import GreenletExit

        class Greenlet(greenlet):
            pass

        initial_refs = sys.getrefcount(Greenlet)
        # This has to be an instance variable because
        # Python 2 raises a SyntaxError if we delete a local
        # variable referenced in an inner scope.
        self.glets = [] # pylint:disable=attribute-defined-outside-init

        def greenlet_main():
            try:
                getcurrent().parent.switch()
            except GreenletExit:
                self.glets.append(getcurrent())

        # Before the
        for _ in range(10):
            Greenlet(greenlet_main).switch()

        del self.glets
        self.assertEqual(sys.getrefcount(Greenlet), initial_refs)

    def test_issue_245_reference_counting_subclass_threads(self):
        # https://github.com/python-greenlet/greenlet/issues/245
        from threading import Thread
        from threading import Event

        from greenlet import getcurrent

        class MyGreenlet(greenlet):
            pass

        glets = []
        ref_cleared = Event()

        def greenlet_main():
            getcurrent().parent.switch()

        def thread_main(greenlet_running_event):
            mine = MyGreenlet(greenlet_main)
            glets.append(mine)
            # The greenlets being deleted must be active
            mine.switch()
            # Don't keep any reference to it in this thread
            del mine
            # Let main know we published our greenlet.
            greenlet_running_event.set()
            # Wait for main to let us know the references are
            # gone and the greenlet objects no longer reachable
            ref_cleared.wait(10)
            # The creating thread must call getcurrent() (or a few other
            # greenlet APIs) because that's when the thread-local list of dead
            # greenlets gets cleared.
            getcurrent()

        # We start with 3 references to the subclass:
        # - This module
        # - Its __mro__
        # - The __subclassess__ attribute of greenlet
        # - (If we call gc.get_referents(), we find four entries, including
        #   some other tuple ``(greenlet)`` that I'm not sure about but must be part
        #   of the machinery.)
        #
        # On Python 3.10 it's often enough to just run 3 threads; on Python 2.7,
        # more threads are needed, and the results are still
        # non-deterministic. Presumably the memory layouts are different
        initial_refs = sys.getrefcount(MyGreenlet)
        thread_ready_events = []
        for _ in range(
                initial_refs + 45
        ):
            event = Event()
            thread = Thread(target=thread_main, args=(event,))
            thread_ready_events.append(event)
            thread.start()


        for done_event in thread_ready_events:
            done_event.wait(10)


        del glets[:]
        ref_cleared.set()
        # Let any other thread run; it will crash the interpreter
        # if not fixed (or silently corrupt memory and we possibly crash
        # later).
        time.sleep(1)
        self.assertEqual(sys.getrefcount(MyGreenlet), initial_refs)


class TestGreenletSetParentErrors(TestCase):
    def test_threaded_reparent(self):
        data = {}
        created_event = threading.Event()
        done_event = threading.Event()

        def run():
            data['g'] = greenlet(lambda: None)
            created_event.set()
            done_event.wait(10)

        def blank():
            greenlet.getcurrent().parent.switch()

        thread = threading.Thread(target=run)
        thread.start()
        created_event.wait(10)
        g = greenlet(blank)
        g.switch()
        with self.assertRaises(ValueError) as exc:
            g.parent = data['g']
        done_event.set()
        thread.join(10)

        self.assertEqual(str(exc.exception), "parent cannot be on a different thread")

    def test_unexpected_reparenting(self):
        another = []
        def worker():
            g = greenlet(lambda: None)
            another.append(g)
            g.switch()
        t = threading.Thread(target=worker)
        t.start()
        t.join(10)
        # The first time we switch (running g_initialstub(), which is
        # when we look up the run attribute) we attempt to change the
        # parent to one from another thread (which also happens to be
        # dead). ``g_initialstub()`` should detect this and raise a
        # greenlet error.
        #
        # EXCEPT: With the fix for #252, this is actually detected
        # sooner, when setting the parent itself. Prior to that fix,
        # the main greenlet from the background thread kept a valid
        # value for ``run_info``, and appeared to be a valid parent
        # until we actually started the greenlet. But now that it's
        # cleared, this test is catching whether ``green_setparent``
        # can detect the dead thread.
        #
        # We need to wait for the cleanup to happen, but we're
        # deliberately leaking a main greenlet here.
        self.wait_for_pending_cleanups(initial_main_greenlets=self.main_greenlets_before_test + 1)

        class convoluted(greenlet):
            def __getattribute__(self, name):
                if name == 'run':
                    self.parent = another[0] # pylint:disable=attribute-defined-outside-init
                return greenlet.__getattribute__(self, name)
        g = convoluted(lambda: None)
        with self.assertRaises(ValueError) as exc:
            g.switch()
        self.assertEqual(str(exc.exception), "parent must not be garbage collected")
        del another[:]

    def test_unexpected_reparenting_thread_running(self):
        # Like ``test_unexpected_reparenting``, except the background thread is
        # actually still alive.
        another = []
        switched_to_greenlet = threading.Event()
        keep_main_alive = threading.Event()
        def worker():
            g = greenlet(lambda: None)
            another.append(g)
            g.switch()
            switched_to_greenlet.set()
            keep_main_alive.wait(10)
        class convoluted(greenlet):
            def __getattribute__(self, name):
                if name == 'run':
                    self.parent = another[0] # pylint:disable=attribute-defined-outside-init
                return greenlet.__getattribute__(self, name)

        t = threading.Thread(target=worker)
        t.start()

        switched_to_greenlet.wait(10)
        try:
            g = convoluted(lambda: None)

            with self.assertRaises(greenlet.error) as exc:
                g.switch()
            self.assertEqual(str(exc.exception), "cannot switch to a different thread")
        finally:
            keep_main_alive.set()
            t.join(10)
            # XXX: Should handle this automatically.
            del another[:]

    def test_cannot_delete_parent(self):
        worker = greenlet(lambda: None)
        self.assertIs(worker.parent, greenlet.getcurrent())

        for g in (worker, greenlet.getcurrent()):
            with self.assertRaises(AttributeError) as exc:
                del g.parent
            self.assertEqual(str(exc.exception), "can't delete attribute")

    def test_main_greenlet_parent_is_none(self):
        # assuming we're in a main greenlet here.
        self.assertIsNone(greenlet.getcurrent().parent)

    def test_set_parent_wrong_types(self):
        def bg():
            # Go back to main.
            greenlet.getcurrent().parent.switch()

        def check(glet):
            for p in None, 1, self, "42":
                with self.assertRaises(TypeError) as exc:
                    glet.parent = p
                self.assertEqual(str(exc.exception), "Expected a greenlet")

        # First, not running
        g = greenlet(bg)
        self.assertFalse(g)
        check(g)

        # Then when running.
        g.switch()
        self.assertTrue(g)
        check(g)

        # Let it finish
        g.switch()

    def _check_trivial_cycle(self, glet):
        with self.assertRaises(ValueError) as exc:
            glet.parent = glet
        self.assertEqual(str(exc.exception), "cyclic parent chain")

    def test_trivial_cycle(self):
        glet = greenlet(lambda: None)
        self._check_trivial_cycle(glet)

    def test_trivial_cycle_main(self):
        self._check_trivial_cycle(greenlet.getcurrent())

    def test_deeper_cycle(self):
        g1 = greenlet(lambda: None)
        g2 = greenlet(lambda: None)
        g3 = greenlet(lambda: None)

        g1.parent = g2
        g2.parent = g3
        with self.assertRaises(ValueError) as exc:
            g3.parent = g1
        self.assertEqual(str(exc.exception), "cyclic parent chain")


class TestRepr(TestCase):

    def assertEndsWith(self, got, suffix):
        self.assertTrue(got.endswith(suffix), (got, suffix))

    def test_main_while_running(self):
        r = repr(greenlet.getcurrent())
        self.assertEndsWith(r, " current active started main>")

    def test_main_in_background(self):
        main = greenlet.getcurrent()
        def run():
            return repr(main)

        g = greenlet(run)
        r = g.switch()
        self.assertEndsWith(r, ' suspended active started main>')

    def test_initial(self):
        r = repr(greenlet())
        self.assertEndsWith(r, ' pending>')

    def test_main_from_other_thread(self):
        main = greenlet.getcurrent()

        class T(threading.Thread):
            original_main = thread_main = None
            main_glet = None
            def run(self):
                self.original_main = repr(main)
                self.main_glet = greenlet.getcurrent()
                self.thread_main = repr(self.main_glet)

        t = T()
        t.start()
        t.join(10)

        self.assertEndsWith(t.original_main, ' suspended active started main>')
        self.assertEndsWith(t.thread_main, ' current active started main>')

        r = repr(t.main_glet)
        # main greenlets, even from dead threads, never really appear dead
        # TODO: Can we find a better way to differentiate that?
        assert not t.main_glet.dead
        self.assertEndsWith(r, ' suspended active started main>')

    def test_dead(self):
        g = greenlet(lambda: None)
        g.switch()
        self.assertEndsWith(repr(g), ' dead>')
        self.assertNotIn('suspended', repr(g))
        self.assertNotIn('started', repr(g))
        self.assertNotIn('active', repr(g))

    def test_formatting_produces_native_str(self):
        # https://github.com/python-greenlet/greenlet/issues/218
        # %s formatting on Python 2 was producing unicode, not str.

        g_dead = greenlet(lambda: None)
        g_not_started = greenlet(lambda: None)
        g_cur = greenlet.getcurrent()

        for g in g_dead, g_not_started, g_cur:

            self.assertIsInstance(
                '%s' % (g,),
                str
            )
            self.assertIsInstance(
                '%r' % (g,),
                str,
            )


class TestMainGreenlet(TestCase):
    # Tests some implementation details, and relies on some
    # implementation details.

    def _check_current_is_main(self):
        # implementation detail
        assert 'main' in repr(greenlet.getcurrent())

        t = type(greenlet.getcurrent())
        assert 'main' in repr(t)
        return t

    def test_main_greenlet_cannot_be_subclassed(self):
        main_type = self._check_current_is_main()
        with self.assertRaises(TypeError):
            class Subclass(main_type): # pylint:disable=unused-variable
                pass

    def test_main_greenlet_is_greenlet(self):
        self._check_current_is_main()
        self.assertIsInstance(greenlet.getcurrent(), greenlet)

if __name__ == '__main__':
    import unittest
    unittest.main()
