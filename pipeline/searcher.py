from copy import deepcopy
from multiprocessing.managers import BaseManager

from RiverOfMutation.POM import PointOfMutation
from organisms.Evaluator import Evaluator


# from organisms.innovation import GlobalInnovations as localInnovations


# NOTE:  this allows implementation of hyperparameter sweep via searchers.
#        Eventually automate this so the environment alters mutation rates.
#        would also like remove distance parameter for PoM radius (atomize).
#
#       need to resolve:
#       RoM structure since edges are no longer finite sample space.
#           |_large mutation rates encroach on higher complexities even with
#           |_fixed timeout. Maybe an edge attribute? this would make degree of
#           |_generalization more complex.
# NOTE: can always use lower level shared Queue if this starts failing or needs
#       simple optimization.
# TODO: shared globalInnovations doesnt want to work. Trace back specific error
class Searcher:
    def __init__(self, timeout, address, fitnessFunction, fitnessObjective):
        """
         a PointOfMutation processor with a timeout/stagnation condition (lifetime) for searching a
         Point of Mutation. Also contains routines for communicating updates and requesting
         a new Point of Mutation from a River of Mutation manager instance upon timeout.

         PARAMETERS:
             timeout: time allotted to search this Point of Mutation
             address: list of strings [address,port] of RoM manager server instance to
                      communicate Searcher swaps and updates

             fitnessFunction: function to be optimized (should be same across all pages for now)
             fitnessObjective: fitness score threshold where function has been solved
         """

        self.timeout = timeout
        self.address = address
        self.fitnessFunction = fitnessFunction
        self.fitnessObjective = fitnessObjective
        # @DEPRECATED
        # self.localInnovations = localInnovations
        self.loadedPOM = None

        # setup river pipeline
        class PageManager(BaseManager):
            pass

        PageManager.register('River')
        # NOTE: it is standard practice for a given application to statically
        #       allocate a port. forwarding allows enough customization
        self.pipeline = PageManager(address=(self.address,
                                             5000), authkey='bada'.encode())
        # self.address[1]),authkey='bada'.encode())
        try:
            # TODO: add some ncurses server stdout backend stuff
            print('connecting..')
            self.pipeline.connect()
            print('connected.\n acquiring RoM shared object..')
            # link a shared river object to this Searcher
            self.river = self.pipeline.River()
            print('RoM acquired.')
            print('RoM object: ', dir(self.river))
        except:
            raise Exception("Could not connect this pipeline to River..")

        # TODO: should extract hyperparameter configuration to callers parameters
        #       since effectively restarting evaluator each time. Would
        #       be more readable since not optimal anyways.
        self.params = {'inputs': 2, 'outputs': 1, 'population': 100,
                       'connectionMutationRate': 0.005, 'nodeMutationRate': 0.001,
                       'weightMutationRate': 0.06, 'weightPerturbRate': 0.9,
                       'selectionPressure': 3}
        self.evaluator = Evaluator(**self.params)
        # TODO: refactor this with respect to self.load need evaluator method for initializing with globalInnovations
        # TODO: this is already performed in self.load()
        # self.evaluator.globalInnovations = self.river.load_map()
        self.load()

    def create_POM(self):
        """
        create a POM with the current genepool.
        """
        # snapshot = deepcopy(self.evaluator.genepool)
        snapshot = self.evaluator.genepool
        mascot = max([x for x in snapshot], key=lambda x: x.fitness)

        if self.loadedPOM is None:
            potential = PointOfMutation(snapshot, mascot, None)
        else:
            # TODO: ensure this deepcopy doesnt lose parent relationship
            #       since parents are defined here and unique this
            #       should be fine. with deepcopy of RoM update/load
            # TODO: this reference is broken. on load PoM is deepcopied
            # potential = PointOfMutation(snapshot, mascot, deepcopy(self.loadedPOM))
            potential = PointOfMutation(snapshot, mascot, self.loadedPOM)

        # TODO: just assign here to self
        return potential

    # TODO: keep localInnovations in Evaluator and pass up to RoM which can
    #       merge into globalInnovations. implement updating evaluators to future
    #       globalInnovations later once features are working
    def load(self):
        """
        load a PointOfMutation into this executor for search from a river shared-object
        pipeline.
        """
        self.loadedPOM = deepcopy(self.river.load())
        if self.loadedPOM is None:
            print('received initial PoM..')
            self.evaluator = Evaluator(**self.params)
            # self.evaluator.globalInnovations = self.river.load_map()
            # @DEPRECATED
            # self.evaluator.genepool = deepcopy(self.initGenepool)
            self.loadedPOM = self.create_POM()
        else:
            print('received {} PoM..'.format(self.loadedPOM))
            # TODO: can serialize evaluator and copy hyperparameters instead of genepool
            #       on swapin
            self.evaluator = Evaluator(**self.params)
            self.evaluator.genepool = self.loadedPOM.swap(len(self.evaluator.genepool))
            # deepcopy innovations to make atomic wrt RoM
            self.evaluator.globalInnovations = deepcopy(self.river.load_map())

    def refresh(self):
        """
        update the RoM with the currently loaded PoM and create a
        new PoM.
        """
        print('refreshing searcher..')
        # submit a deepcopy PoM for comparison in RoM. this is fine since mascot
        # is used for comparison so PoM optimization is the same as merge
        self.river.update(deepcopy(self.loadedPOM))

        # TODO: ensure this is not the previous PoM reference
        #       probably dont need to create a new POM since
        #       deepcopying and using mascot comparison for RoM
        #       tree operations
        fresh = self.create_POM()
        self.loadedPOM = fresh
        return self.exec()

    # TODO: load in merged solution on RoM merge (this all needs to be traced
    #       against RoM tree operations and structure
    def exec(self):
        """
        search for timeout generations, updating the river when the condition is met and
        retrieving a new PointOfMutation when timeout is reached unless a new POM
        is discovered.
        """
        for time in range(0, self.timeout):
            print('searching.. {}'.format(time))
            self.evaluator.nextGeneration(self.fitnessFunction)

            # TODO: dont call create_POM every time.
            # potential = self.create_POM()

            # merge PoM condition
            # check if locally justified complexification
            # TODO: refactor these conditions
            if any([x.fitness > self.loadedPOM.mascot.fitness for x in self.evaluator.genepool]):
                # TODO: should wait for update to see if this searcher is far behind others and should
                #       load instead of recurse. not critical if this is a very inferior solution
                #       the likelihood of timeout in the next evaluation is higher
                return self.refresh()

            # terminal condition
            if any([x.fitness > self.fitnessObjective
                    for x in self.evaluator.genepool]):
                # keep searching since alternate conventions still exist
                print('search complete.')
                return self.refresh()

        # timeout has occurred, request a new PoM to search
        print('search timeout, restarting..')
        self.load()
        return self.exec()
